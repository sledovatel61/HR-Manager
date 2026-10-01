"""Вложения в карточке кандидата: список, загрузка, скачивание, удаление.

Правила доступа (проверяются на сервере на каждый запрос):

* без сессии — 401; на загрузке и удалении обязателен CSRF-токен (метод
  меняет состояние, это делает общий слой ``app.deps``);
* вложения видит тот, кому сервер разрешает видеть самого кандидата:
  HR — только своих кандидатов, руководитель и администратор — всех, плюс
  явный грант ``candidate_documents_all``/``pilot_full_access``. Чужой
  кандидат отвечает 404, чтобы не раскрывать факт его существования;
* загрузка и удаление доступны тому же кругу лиц (ответственный HR,
  руководитель, администратор, держатель гранта) — скрытие кнопки во frontend
  защитой не считается;
* скачивание идёт только через этот endpoint: файлы не лежат в публичном
  каталоге и не раздаются nginx.

Коды ответов: 401 (нет сессии), 403 (нет прав/вложения выключены),
404 (кандидат или вложение недоступны), 409 (имя уже занято, вложение уже
удалено), 413 (превышен размер/квота), 415 (формат не поддерживается),
422 (пустой файл).
"""

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.candidate_attachments import (
    MIME_BY_KIND,
    AttachmentLimits,
    AttachmentRejected,
    can_manage,
    can_view,
    content_disposition,
    create_attachment,
    delete_attachment,
    ensure_enabled,
    get_attachment,
    iter_content,
    list_attachments,
    read_payload,
    read_upload_limited,
    record_download,
)
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, get_settings_from_request
from app.models import Candidate, User
from app.schemas import (
    AttachmentLimitsOut,
    CandidateAttachmentList,
    CandidateAttachmentOut,
)
from app.utils import client_ip, user_agent

router = APIRouter(prefix="/candidates/{candidate_id}/attachments", tags=["attachments"])


def _reject(exc: AttachmentRejected) -> HTTPException:
    """Превратить отказ доменного слоя в HTTP-ответ с безопасным текстом."""
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _candidate(db: Session, candidate_id: str, user: User) -> Candidate:
    """Кандидат, доступный пользователю; иначе 404 (без утечки существования)."""
    try:
        parsed = UUID(candidate_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден."
        ) from None
    candidate = db.get(Candidate, parsed)
    if candidate is None or not can_view(db, user, candidate):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    return candidate


def _attachment_id(candidate_id: str, attachment_id: str) -> UUID:
    try:
        return UUID(attachment_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Вложение не найдено."
        ) from None


@router.get("", response_model=CandidateAttachmentList)
def list_candidate_attachments(
    candidate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_from_request),
) -> CandidateAttachmentList:
    """Активные вложения кандидата и действующие лимиты (без байтов)."""
    candidate = _candidate(db, candidate_id, user)
    items = list_attachments(db, candidate.id)
    limits = AttachmentLimits.from_settings(settings)
    return CandidateAttachmentList(
        items=[CandidateAttachmentOut.model_validate(item) for item in items],
        total=len(items),
        total_bytes=sum(item.size_bytes for item in items),
        limits=AttachmentLimitsOut(
            max_file_bytes=limits.max_file_bytes,
            max_total_bytes=limits.max_total_bytes,
            max_count=limits.max_count,
        ),
        can_manage=can_manage(db, user, candidate),
    )


@router.post("", response_model=CandidateAttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    candidate_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_from_request),
) -> CandidateAttachmentOut:
    """Загрузить анкету/скан.

    Файл читается потоково с жёстким лимитом, затем проверяются сигнатура,
    контейнер и квоты кандидата; имя очищается, путь из него не строится.
    """
    candidate = _candidate(db, candidate_id, user)
    if not can_manage(db, user, candidate):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для загрузки вложений этого кандидата.",
        )
    try:
        ensure_enabled(settings)
        payload = await read_upload_limited(file, settings.attachments_max_file_bytes)
        attachment = create_attachment(
            db,
            candidate=candidate,
            actor=user,
            payload=payload,
            filename=file.filename,
            content_type=file.content_type,
            settings=settings,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
        )
    except AttachmentRejected as exc:
        raise _reject(exc) from exc
    return CandidateAttachmentOut.model_validate(attachment)


@router.get("/{attachment_id}/download")
def download_attachment(
    candidate_id: str,
    attachment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Отдать байты вложения как скачиваемый файл.

    Всегда ``Content-Disposition: attachment`` — встроенного просмотра нет,
    поэтому содержимое документа не исполняется в origin приложения. Имя
    файла берётся из сохранённых метаданных, внутренний путь не отдаётся.
    """
    candidate = _candidate(db, candidate_id, user)
    try:
        attachment = get_attachment(db, candidate, _attachment_id(candidate_id, attachment_id))
        # Байты читаются здесь, а не внутри генератора: сессия запроса
        # закрывается до отправки тела ответа.
        payload = read_payload(attachment)
        size_bytes = attachment.size_bytes
        filename = attachment.filename
        kind = attachment.kind
        record_download(
            db,
            candidate=candidate,
            attachment=attachment,
            actor=user,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
        )
    except AttachmentRejected as exc:
        raise _reject(exc) from exc
    return StreamingResponse(
        iter_content(payload),
        media_type=MIME_BY_KIND[kind],
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(size_bytes),
            # Двойная защита к глобальному nosniff: браузер не должен
            # «угадывать» тип содержимого чужого документа.
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/{attachment_id}", status_code=status.HTTP_200_OK)
def remove_attachment(
    candidate_id: str,
    attachment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, bool]:
    """Мягкое удаление вложения с аудитом (байты остаются в резервных копиях)."""
    candidate = _candidate(db, candidate_id, user)
    if not can_manage(db, user, candidate):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления вложений этого кандидата.",
        )
    try:
        attachment = get_attachment(db, candidate, _attachment_id(candidate_id, attachment_id))
        delete_attachment(
            db,
            candidate=candidate,
            attachment=attachment,
            actor=user,
            ip_address=client_ip(request),
            user_agent=user_agent(request.headers),
        )
    except AttachmentRejected as exc:
        raise _reject(exc) from exc
    return {"deleted": True}
