import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.transaction import Transaction
from app.schemas.transaction import (
    CaptureOrderRequest,
    CreateOrderRequest,
    PayPalCaptureResponse,
    PayPalOrderResponse,
    TransactionResponse,
)
from app.services.paypal import PayPalAPIError, PayPalService, get_paypal_service

router = APIRouter(prefix="/paypal", tags=["PayPal"])


def _extract_approval_url(order_data: dict) -> Optional[str]:
    links = order_data.get("links", [])
    for link in links:
        if link.get("rel") == "payer-action" or link.get("rel") == "approve":
            return link.get("href")
    return None


def _extract_capture_id(capture_data: dict) -> Optional[str]:
    purchase_units = capture_data.get("purchase_units", [])
    if purchase_units:
        payments = purchase_units[0].get("payments", {})
        captures = payments.get("captures", [])
        if captures:
            return captures[0].get("id")
    return capture_data.get("id")


@router.post(
    "/orders",
    response_model=PayPalOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a PayPal Sandbox order",
    description="Creates a local transaction record and places a v2 order on PayPal Sandbox rails.",
)
async def create_order(
    request: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
    paypal_service: PayPalService = Depends(get_paypal_service),
) -> PayPalOrderResponse:
    # Step 1: Pre-persist local transaction
    tx = Transaction(
        id=str(uuid.uuid4()),
        amount=request.amount,
        currency=request.currency,
        status="PENDING",
        merchant_name=request.merchant_name,
        description=request.description,
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)

    # Step 2: Call PayPal API (Law 5: No database lock held across network call)
    try:
        order_data = await paypal_service.create_order(
            amount=request.amount,
            currency=request.currency,
            custom_id=tx.id,
            description=request.description,
        )
    except PayPalAPIError as e:
        tx.status = "FAILED"
        await db.commit()
        raise HTTPException(
            status_code=e.status_code if 400 <= e.status_code < 500 else status.HTTP_502_BAD_GATEWAY,
            detail={"message": e.message, "details": e.details},
        )
    except Exception as e:
        tx.status = "FAILED"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error communicating with PayPal: {str(e)}",
        )

    # Step 3: Update local transaction with PayPal Order ID and status
    paypal_order_id = order_data.get("id")
    tx.paypal_order_id = paypal_order_id
    tx.status = "CREATED"
    await db.commit()
    await db.refresh(tx)

    return PayPalOrderResponse(
        order_id=paypal_order_id,
        status=tx.status,
        approval_url=_extract_approval_url(order_data),
        transaction_id=tx.id,
        amount=tx.amount,
        currency=tx.currency,
    )


@router.post(
    "/orders/{order_id}/capture",
    response_model=PayPalCaptureResponse,
    status_code=status.HTTP_200_OK,
    summary="Capture an authorized PayPal order",
    description="Captures the funds for an existing PayPal Sandbox order and persists capture ID.",
)
async def capture_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    paypal_service: PayPalService = Depends(get_paypal_service),
) -> PayPalCaptureResponse:
    stmt = select(Transaction).where(Transaction.paypal_order_id == order_id)
    result = await db.execute(stmt)
    tx = result.scalar_one_or_none()

    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction with PayPal Order ID '{order_id}' not found.",
        )

    if tx.status == "CAPTURED" and tx.paypal_capture_id:
        return PayPalCaptureResponse(
            capture_id=tx.paypal_capture_id,
            order_id=tx.paypal_order_id,
            status=tx.status,
            transaction_id=tx.id,
            amount=tx.amount,
            currency=tx.currency,
        )

    try:
        capture_data = await paypal_service.capture_order(order_id)
    except PayPalAPIError as e:
        tx.status = "PAYPAL_FAILED"
        await db.commit()
        raise HTTPException(
            status_code=e.status_code if 400 <= e.status_code < 500 else status.HTTP_502_BAD_GATEWAY,
            detail={"message": e.message, "details": e.details},
        )
    except Exception as e:
        tx.status = "PAYPAL_FAILED"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error capturing PayPal order: {str(e)}",
        )

    capture_id = _extract_capture_id(capture_data) or "UNKNOWN_CAPTURE_ID"
    paypal_status = capture_data.get("status", "COMPLETED")

    tx.paypal_capture_id = capture_id
    tx.status = "CAPTURED" if paypal_status == "COMPLETED" else paypal_status
    await db.commit()
    await db.refresh(tx)

    return PayPalCaptureResponse(
        capture_id=tx.paypal_capture_id,
        order_id=tx.paypal_order_id,
        status=tx.status,
        transaction_id=tx.id,
        amount=tx.amount,
        currency=tx.currency,
    )


@router.get(
    "/transactions",
    response_model=List[TransactionResponse],
    summary="List transactions",
    description="Retrieve all persisted transactions with order and capture details.",
)
async def list_transactions(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> List[TransactionResponse]:
    stmt = (
        select(Transaction)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    transactions = result.scalars().all()
    return [TransactionResponse.model_validate(t) for t in transactions]


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionResponse,
    summary="Get transaction details",
    description="Retrieve details for a single transaction by its Guardian ID.",
)
async def get_transaction(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
) -> TransactionResponse:
    stmt = select(Transaction).where(Transaction.id == transaction_id)
    result = await db.execute(stmt)
    tx = result.scalar_one_or_none()

    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found.",
        )

    return TransactionResponse.model_validate(tx)
