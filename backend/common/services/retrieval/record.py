from typing import Optional, Dict
from common.models import Collection, Record, RecordType, SortOrderEnum, ListResult, Model
from common.database_ops import record as db_record
from common.error import ErrorCode, raise_http_error
from .collection import validate_and_get_collection
from common.services.model.model import get_model
from .embedding import embed_documents

__all__ = [
    "list_records",
    "create_record",
    "update_record",
    "get_record",
    "delete_record",
]


async def validate_and_get_record(postgres_conn, collection: Collection, record_id: str) -> Record:
    record = await db_record.get_record(postgres_conn, collection, record_id)
    if not record:
        raise_http_error(ErrorCode.OBJECT_NOT_FOUND, message=f"Record {record_id} not found.")
    return record


async def list_records(
    postgres_conn,
    collection_id: str,
    limit: int,
    order: SortOrderEnum,
    after: Optional[str],
    before: Optional[str],
    offset: Optional[int],
    id_search: Optional[str],
    name_search: Optional[str],
) -> ListResult:
    """
    List records
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    :param limit: the limit of the query
    :param order: the order of the query, asc or desc
    :param after: the cursor ID to query after
    :param before: the cursor ID to query before
    :param offset: the offset of the query
    :param id_search: the record ID to search for
    :param name_search: the record name to search for
    :return: a list of records, total count of records, and whether there are more records
    """

    # validate collection
    collection = await validate_and_get_collection(postgres_conn, collection_id=collection_id)

    # validate after and before
    after_record, before_record = None, None

    if after:
        after_record = await validate_and_get_record(postgres_conn, collection, after)

    if before:
        before_record = await validate_and_get_record(postgres_conn, collection, before)

    return await db_record.list_records(
        postgres_conn=postgres_conn,
        collection=collection,
        limit=limit,
        order=order,
        after_record=after_record,
        before_record=before_record,
        offset=offset,
        prefix_filters={
            "record_id": id_search,
            "name": name_search,
        },
    )


async def create_record(
    postgres_conn,
    collection_id: str,
    title: str,
    type: RecordType,
    content: str,
    metadata: Dict[str, str],
) -> Record:
    """
    Create record
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    :param title: the record title
    :param type: the record type
    :param content: the record content
    :param metadata: the record metadata
    :return: the created record
    """

    # validate collection
    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id=collection_id)

    # validate model
    embedding_model: Model = await get_model(postgres_conn, collection.embedding_model_id)

    # split content into chunks
    documents = []

    if type == RecordType.TEXT:
        content = content.strip()
        if not content:
            raise_http_error(ErrorCode.REQUEST_VALIDATION_ERROR, message="Content cannot be empty.")
        documents = collection.text_splitter.split_text(content)
    else:
        raise NotImplementedError(f"Record type {type} is not supported yet.")

    # embed the documents
    embeddings = await embed_documents(
        documents=documents,
        embedding_model=embedding_model,
        embedding_size=collection.embedding_size,
    )

    # create record
    record = await db_record.create_record_and_chunks(
        postgres_conn=postgres_conn,
        collection=collection,
        chunk_texts=documents,
        chunk_embeddings=embeddings,
        title=title,
        type=type,
        content=content,
        metadata=metadata,
    )
    return record


async def update_record(
    postgres_conn,
    collection_id: str,
    record_id: str,
    metadata: Optional[Dict[str, str]],
) -> Record:
    # todo: support record content update

    """
    Update record
    :param postgres_conn: postgres connection
    :param record_id: the record id
    :param metadata: the record metadata to update
    :return: the updated record
    """

    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id=collection_id)
    record: Record = await validate_and_get_record(postgres_conn, collection=collection, record_id=record_id)

    update_dict = {}

    if metadata:
        update_dict["metadata"] = metadata

    if update_dict:
        record = await db_record.update_record(
            conn=postgres_conn,
            collection=collection,
            record=record,
            update_dict=update_dict,
        )

    return record


async def get_record(postgres_conn, collection_id: str, record_id: str) -> Record:
    """
    Get record
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    :param record_id: the record id
    :return: the record
    """
    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id=collection_id)
    record: Record = await validate_and_get_record(postgres_conn, collection, record_id)
    return record


async def delete_record(postgres_conn, collection_id: str, record_id: str) -> None:
    """
    Delete record
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    :param record_id: the record id
    """
    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id=collection_id)
    record: Record = await validate_and_get_record(postgres_conn, collection, record_id)
    await db_record.delete_record(postgres_conn, record)
