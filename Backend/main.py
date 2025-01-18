import uvicorn
from fastapi import FastAPI, HTTPException, status, Depends, responses, security, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import chatbot, user
app = FastAPI()

# Include routers
app.include_router(chatbot.router, prefix="/chatbot", tags=["chatbot"])
app.include_router(user.router, prefix="/user", tags=["user"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    print("Running !!!!")
    uvicorn.run(app, host="0.0.0.0", port=8000)