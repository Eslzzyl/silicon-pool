from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/")
async def root():
    return FileResponse("static/index.html")


@router.get("/login")
async def login():
    return FileResponse("static/login.html")
