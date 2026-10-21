import uuid

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile
from sqlmodel import col, func, select

from app import knowledge
from app.api.deps import CurrentUser, SessionDep
from app.knowledge import (
    DocumentChunkPublic,
    DocumentDetail,
    DocumentPublic,
    DocumentsPublic,
    DocumentUploadResponse,
    KnowledgeError,
    SearchRequest,
    SearchResponse,
)
from app.models import Document, DocumentChunk, Message, User

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentsPublic)
def read_documents(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=20),
) -> DocumentsPublic:
    statement = select(Document).where(Document.owner_id == current_user.id)
    documents = session.exec(
        statement.order_by(col(Document.created_at).desc()).offset(skip).limit(limit)
    ).all()
    count = session.exec(
        select(func.count())
        .select_from(Document)
        .where(Document.owner_id == current_user.id)
    ).one()
    return DocumentsPublic(
        data=[DocumentPublic.model_validate(doc) for doc in documents], count=count
    )


@router.post("", response_model=DocumentUploadResponse, status_code=201)
def upload_document(
    session: SessionDep, current_user: CurrentUser, file: UploadFile, response: Response
) -> DocumentUploadResponse:
    try:
        name, text, digest = knowledge.parse_document(
            file.filename or "", file.file.read(knowledge.MAX_FILE_BYTES + 1)
        )
        chunks = knowledge.split_document(text)
    except KnowledgeError as exc:
        raise HTTPException(
            exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from None
    finally:
        file.file.close()
    # Serialize imports for this owner: concurrent requests cannot bypass the quota.
    session.exec(select(User).where(User.id == current_user.id).with_for_update()).one()
    duplicate = session.exec(
        select(Document).where(
            Document.owner_id == current_user.id, Document.sha256 == digest
        )
    ).first()
    if duplicate:
        response.status_code = 200
        return DocumentUploadResponse(
            document=DocumentPublic.model_validate(duplicate), duplicate=True
        )
    count = session.exec(
        select(func.count())
        .select_from(Document)
        .where(Document.owner_id == current_user.id)
    ).one()
    if count >= knowledge.MAX_DOCUMENTS_PER_USER:
        raise HTTPException(
            409,
            detail={
                "code": "document_quota",
                "message": "每位用户最多保存 20 份文档，请先删除不再需要的文档。",
            },
        )
    document = Document(
        owner_id=current_user.id,
        filename=name,
        sha256=digest,
        size_bytes=len(text.encode()),
        chunk_count=len(chunks),
    )
    session.add(document)
    session.flush()
    for position, chunk in enumerate(chunks):
        session.add(
            DocumentChunk(
                document_id=document.id,
                position=position,
                heading=chunk.heading,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                content=chunk.content,
            )
        )
    session.commit()
    session.refresh(document)
    return DocumentUploadResponse(
        document=DocumentPublic.model_validate(document), duplicate=False
    )


@router.post("/search", response_model=SearchResponse)
def search_document_chunks(
    session: SessionDep, current_user: CurrentUser, body: SearchRequest
) -> SearchResponse:
    try:
        return knowledge.search_result(
            session,
            current_user.id,
            body.query,
            body.limit,
            body.document_id,
            body.method,
        )
    except KnowledgeError as exc:
        raise HTTPException(
            exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from None


@router.get("/{id}", response_model=DocumentDetail)
def read_document(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> DocumentDetail:
    try:
        document = knowledge.owned_document(session, current_user.id, id)
    except KnowledgeError as exc:
        raise HTTPException(
            exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from None
    chunks = session.exec(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(col(DocumentChunk.position))
    ).all()
    return DocumentDetail(
        document=DocumentPublic.model_validate(document),
        chunks=[DocumentChunkPublic.model_validate(chunk) for chunk in chunks],
    )


@router.delete("/{id}", response_model=Message)
def delete_document(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    document = session.exec(
        select(Document).where(Document.id == id, Document.owner_id == current_user.id)
    ).first()
    if not document:
        raise HTTPException(404, detail="文档不存在。")
    session.delete(document)
    session.commit()
    return Message(message="文档及其片段已删除。")
