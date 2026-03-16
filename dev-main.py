import base64
import io
import os
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from pandas.io.sas.sas_constants import magic
from pdf2image import convert_from_bytes
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from src.editor import add_png_pdfrw, convert_scanned_pdf_to_pdf, create_image_reader


class Result(BaseModel):
    page: int | None = None
    signature_new: str | None = None
    positions: list | None = None
    handwritten_total: list | None = None
    page_size: dict | None = None


APP = FastAPI(
    title="PDF Signer",
    version="1.0.0",
)

APP.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ASSETS_DIR = Path(__file__).parent / "assets"
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Монтируем статические файлы
APP.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# Шаблоны
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@APP.get("/")
async def start_editor(request: Request,
                       session_id: str | None = None):
    # Добавляем подпись, если есть
    has_sign = False
    sign_fp = Path('none')
    sign = ''
    if sign_fp.exists():
        has_sign = True
        with open(sign_fp, 'rb') as f:
            encoded = base64.b64encode(f.read()).decode()
            sign = f'data:image/png;base64,{encoded}'
    if session_id is None:
        session_id = str(uuid.uuid4())
        return templates.TemplateResponse(
            request=request,
            name="dev-editor.html",
            context={
                "user": '',
                "chat_id": '',
                "session_id": session_id,
                'has_sign': has_sign,
                'sign': sign,
                "version": "1.0.2"
            }
        )

    return templates.TemplateResponse(
        request=request, name="dev-editor.html",
        context={
            "session_id": session_id,
            "chat_id": f'',
            "user": '',
            'has_sign': has_sign,
            'sign': sign,
            'version': '1.0.2'}
    )


@APP.get("/preform_nextcloud")
async def preform_nextcloud():
    """Сохранение результата в Nextcloud"""

    # Сохраняем в домашнюю директорию пользователя
    try:
        # Получаем путь к папке Documents или создаем свою
        preforms_dir = f"/Подпись документов/заготовки"

        files = []

        return JSONResponse(content={
            "preforms": files
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@APP.get("/payload/")
async def get_payload(
        session_id: str,
):
    """Получение данных сессии"""

    return JSONResponse(content={})


@APP.post("/upload")
async def upload_file(
        request: Request,
        file: UploadFile = File(...),
):
    """Загрузка файла с фронтенда для редактирования"""
    # Проверяем размер файла (например, максимум 50MB)
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    # Проверяем, что это PDF
    # Способ 1: по расширению
    if not file.filename.lower().endswith('.pdf'):
        # Способ 2: по MIME типу (более надежно)
        mime_type = magic.from_buffer(contents, mime=True)
        if mime_type != 'application/pdf':
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Генерируем ID сессии

    images = convert_from_bytes(contents)
    payload = []
    for num in range(len(images)):
        img = images[num]

        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        encoded_data = base64.b64encode(img_byte_arr)
        item = {
            'page': num,
            'size': img.size,
            'data': 'data:image/png;base64,' + encoded_data.decode(),
        }
        if img.size[0] < img.size[1]:
            item['orientation'] = 'portrait'
        else:
            item['orientation'] = 'landscape'
        payload.append(item)
    if len(payload) == 0:
        raise HTTPException(status_code=400, detail="не удалось получить файл")

    with open('tmp/payload.pdf', 'wb') as w:
        w.write(contents)
    return JSONResponse(content={'payload': payload, 'session_id': '', 'sign': ''})


@APP.post("/document-result")
async def result(result: Result,
                 session_id: str | None = None):
    res = result.model_dump()
    new_sign = res.get('signature_new')
    page = res.get('page')
    positions = res.get('positions')
    handwritten = res.get('handwritten_total')
    page_size = res.get('page_size')
    page_w = page_size.get('w')
    page_h = page_size.get('h')


    with open('tmp/payload.pdf', 'rb') as rdr:
        signet = rdr.read()
    signet = convert_scanned_pdf_to_pdf(signet)
    for hw in handwritten:
        top = hw.get('top')
        left = hw.get('left')
        width = hw.get('width')
        height = hw.get('height')
        written = hw.get('base64')
        page_num = hw.get('page')
        im_bytes = base64.b64decode(written.replace('data:image/png;base64,', ''))
        imgredr = create_image_reader(io.BytesIO(im_bytes))
        signet = add_png_pdfrw(
            image=imgredr,
            input_data=signet,
            size=(width, height),
            position=(left, top),
            page_number=page_num,
            page_size=(page_w, page_h)
        )

    sig_stamp_name = 'r_sig_stamp.pdf'

    return StreamingResponse(
        io.BytesIO(signet),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={sig_stamp_name}"}
    )


if __name__ == "__main__":
    uvicorn.run(APP, host='localhost', port=9003)
