from fastapi import APIRouter
from app.api.v1.agents import router as agents_router
from app.api.v1.budgets import router as budgets_router
from app.api.v1.paypal import router as paypal_router
from app.api.v1.policies import router as policies_router
from app.api.v1.reservations import router as reservations_router
from app.api.v1.spend import router as spend_router

api_router = APIRouter()
api_router.include_router(spend_router)
api_router.include_router(reservations_router)
api_router.include_router(agents_router)
api_router.include_router(policies_router)
api_router.include_router(budgets_router)
api_router.include_router(paypal_router)
