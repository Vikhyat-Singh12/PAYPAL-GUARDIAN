from fastapi import FastAPI

app = FastAPI(title="PayPal Guardian API")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "guardian"}