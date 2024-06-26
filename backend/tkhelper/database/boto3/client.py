import io
import logging
import os
from typing import Dict, Optional, Tuple

import aioboto3
import aiofiles
import aiofiles.os
from app.config import CONFIG

from tkhelper.error import ErrorCode, raise_http_error
from tkhelper.utils import decode_base64_to_text, encode_text_to_base64, get_base62_date

logger = logging.getLogger(__name__)


def _validate_file_id(file_id: str) -> Tuple[str, str]:
    """
    Validate file_id
    :param file_id: the id of the file, e.g. txt_x123456
    :return: the extension and the id
    """
    parts = file_id.split("_")
    if len(parts) != 2:
        raise_http_error(
            ErrorCode.REQUEST_VALIDATION_ERROR,
            f"Invalid file_id: {file_id}",
        )
    return parts[0], parts[1]


def _object_key(
    purpose: str, file_id: str, ext: str, tenant_id: str, timestamp_path: bool = True
) -> str:
    if timestamp_path:
        return f"{purpose}/{tenant_id}/{get_base62_date()}/{file_id}.{ext}"
    return f"{purpose}/{tenant_id}/{file_id}.{ext}"


class StorageClient:
    def __init__(
        self,
        service_name: str,
        endpoint_url: Optional[str],
        access_key_id: Optional[str],
        access_key_secret: Optional[str],
        timestamp_path: bool = True,
    ):
        if endpoint_url and access_key_id and access_key_secret:
            self._session = aioboto3.Session(
                aws_access_key_id=access_key_id, aws_secret_access_key=access_key_secret
            )
        elif CONFIG.PATH_TO_VOLUME:
            self._session = None
            self._volume = os.path.abspath(CONFIG.PATH_TO_VOLUME)
        else:
            raise ValueError("Missing storage credentials.")

        self._service_name = service_name
        self._endpoint_url = endpoint_url
        self._timestamp_path = timestamp_path

    async def init(self):
        logger.info("Storage client initialized.")

    async def close(self):
        logger.info("Storage client closed.")

    async def clean_data(self):
        pass

    async def health_check(self) -> bool:
        return True

    async def upload_file_from_path(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        file_path: str,
        tenant_id: str,
        metadata: Dict = {},
    ):
        """
        Upload file to minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file, e.g. txt_x123456
        :param file_path: the path of the file
        :param tenant_id: the id of the tenant
        :param metadata: the metadata of the file, a dict
        """

        async with aiofiles.open(file_path, "rb") as file:
            content_bytes = await file.read()
            original_file_name = os.path.basename(file_path)
            await self.upload_file_from_bytes(
                bucket_name=bucket_name,
                purpose=purpose,
                file_id=file_id,
                content_bytes=content_bytes,
                original_file_name=original_file_name,
                tenant_id=tenant_id,
                metadata=metadata,
            )

    async def upload_file_from_bytes(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        content_bytes: bytes,
        original_file_name: str,
        tenant_id: str,
        metadata: Dict = {},
        return_url: bool = False,
    ):
        """
        Upload file to minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file, e.g. txt_x123456
        :param content_bytes: the content of the file
        :param original_file_name: the original file name
        :param tenant_id: the id of the tenant
        :param metadata: the metadata of the file, a dict
        :return: True if the file is uploaded successfully, False otherwise
        """

        metadata = metadata or {}
        metadata["base64_file_name"] = encode_text_to_base64(
            original_file_name, exclude_padding=True
        )
        metadata["file_size"] = str(len(content_bytes))
        metadata["tenant_id"] = tenant_id

        try:
            ext, _id = _validate_file_id(file_id)
            key = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
            """ upload_fileobj params
            :param Fileobj: BinaryIO
            :param Bucket: str
            :param Key: str
            :param ExtraArgs: Optional[Dict[str, Any]] = None
            :param Callback: Optional[Callable[[int], None]] = None
            :param Config: Optional[S3TransferConfig] = None    # boto3.s3.transfer.TransferConfig
            :param Processing: Callable[[bytes], bytes] = None
            """
            if self._session:
                async with self._session.client(
                    service_name=self._service_name, endpoint_url=self._endpoint_url
                ) as client:
                    await client.upload_fileobj(
                        Fileobj=io.BytesIO(content_bytes),
                        Bucket=bucket_name,
                        Key=key,
                        ExtraArgs={"Metadata": metadata or {}},
                    )
                if return_url:
                    return f"{self._endpoint_url}/{bucket_name}/{key}"
            else:
                await self.save_to_volume(file_bytes=content_bytes, path=key)
                if return_url:
                    return f"{self._volume}/{key}"
            return True
        except Exception as e:
            logger.debug(
                f"upload_file_from_bytes: failed to upload file {file_id}, e={e}"
            )
            return False

    async def check_file_exists(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        tenant_id: str,
    ) -> bool:
        """
        Check if file exists in minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file
        :param tenant_id: the id of the tenant. Error will be raised if the tenant_id is not valid
        :return: the metadata of the file, a dict
        """

        # check if metadata exists
        try:
            ext, _id = _validate_file_id(file_id)
            key = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
            if self._session:
                async with self._session.client(
                    service_name=self._service_name, endpoint_url=self._endpoint_url
                ) as client:
                    await client.head_object(
                        Bucket=bucket_name,
                        Key=key,
                    )
            else:
                await self.check_volume_file_exists(path=key)

            return True
        except Exception as e:
            logger.debug(f"check_file_exists: failed to check file {file_id}, e={e}")
            return False

    async def get_file_metadata(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        tenant_id: str,
    ) -> Dict:
        """
        Get metadata from minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file
        :param tenant_id: the id of the tenant. Error will be raised if the tenant_id is not valid
        :return: the metadata of the file, a dict
        """

        ext, _id = _validate_file_id(file_id)
        key = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
        try:
            if self._session:
                async with self._session.client(
                    service_name=self._service_name, endpoint_url=self._endpoint_url
                ) as client:
                    response = await client.head_object(
                        Bucket=bucket_name,
                        Key=key,
                    )
                file_metadata = response["Metadata"]
                base64_name = file_metadata.pop("base64_file_name", None)
                if base64_name:
                    file_metadata["original_file_name"] = decode_base64_to_text(
                        base64_name
                    )
                return file_metadata
            return await self.get_volume_file_metadata(path=key)
        except Exception as e:
            logger.debug(f"get_file_metadata: failed to get file {file_id}, e={e}")
            raise_http_error(ErrorCode.OBJECT_NOT_FOUND, f"File {file_id} not found")

    async def download_file_to_bytes(
        self, bucket_name: str, purpose: str, file_id: str, tenant_id: str
    ) -> bytes:
        """
        Download file from minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file, e.g. txt_x123456
        :param tenant_id: the id of the tenant. Error will be raised if the tenant_id is not valid
        """

        ext, _id = _validate_file_id(file_id)
        key = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
        try:
            if self._session:
                async with self._session.client(
                    service_name=self._service_name, endpoint_url=self._endpoint_url
                ) as client:
                    response = await client.get_object(
                        Bucket=bucket_name,
                        Key=key,
                    )
                data = await response["Body"].read()
                logger.debug(
                    f"download_file_to_bytes: downloaded Minio file to bytes: {_id}.{ext}"
                )
                return data

            return await self.read_volume_file(path=key)
        except Exception:
            logger.debug(f"download_file_to_bytes: failed to download file {file_id}")
            raise_http_error(ErrorCode.OBJECT_NOT_FOUND, "Failed to download file")

    async def download_file_to_path(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        file_path: str,
        tenant_id: str,
    ) -> bool:
        """
        Download file from minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file, e.g. txt_x123456
        :param file_path: the path of the file
        :param tenant_id: the id of the tenant. Error will be raised if the tenant_id is not valid
        :return: True if the file is downloaded successfully, False otherwise
        """
        ext, _id = _validate_file_id(file_id)
        key = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
        # download file
        try:
            if self._session:
                async with self._session.client(
                    service_name=self._service_name, endpoint_url=self._endpoint_url
                ) as client:
                    response = await client.get_object(
                        Bucket=bucket_name,
                        Key=key,
                    )
                    file_bytes = await response["Body"].read()
            else:
                file_bytes = await self.read_volume_file(path=key)

        except Exception:
            logger.debug(f"download_file_to_bytes: failed to download file {file_id}")
            raise_http_error(ErrorCode.OBJECT_NOT_FOUND, "Failed to download file")

        # create directory if not exists
        file_dir = file_path[: file_path.rfind("/")]
        if not await aiofiles.os.path.exists(file_dir):
            await aiofiles.os.makedirs(file_dir)

        # save file
        try:
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(file_bytes)
                logger.debug(
                    f"download_file_to_path: saved Minio file {file_id} to {file_path}"
                )
            return True
        except Exception as e:
            logger.error(
                f"download_file_to_path: failed to save Minio file {file_id} to {file_path}, e={e}"
            )

        return False

    def get_file_url(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        tenant_id: str,
    ) -> str:
        """
        Get file url from minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file, e.g. txt_x123456
        :param tenant_id: the id of the tenant. Error will be raised if the tenant_id is not valid
        :return: the url of the file
        """

        ext, _id = _validate_file_id(file_id)
        object_name = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
        if self._session:
            return f"http://{self._endpoint_url}/{bucket_name}/{object_name}"

        return f"{self._volume}/{object_name}"

    async def delete_file(
        self,
        bucket_name: str,
        purpose: str,
        file_id: str,
        tenant_id: str,
    ) -> bool:
        """
        Delete file from minio
        :param bucket_name: the name of the bucket
        :param purpose: the purpose of the file
        :param file_id: the id of the file, e.g. txt_x123456
        :param tenant_id: the id of the tenant. Error will be raised if the tenant_id is not valid
        :return: True if the file is deleted successfully, False otherwise
        """

        ext, _id = _validate_file_id(file_id)
        key = _object_key(purpose, _id, ext, tenant_id, self._timestamp_path)
        if not await self.check_file_exists(bucket_name, purpose, file_id, tenant_id):
            raise_http_error(ErrorCode.OBJECT_NOT_FOUND, f"File {file_id} not found")
        try:
            if self._session:
                async with self._session.client(
                    service_name=self._service_name, endpoint_url=self._endpoint_url
                ) as client:
                    await client.delete_object(
                        Bucket=bucket_name,
                        Key=key,
                    )
            else:
                await self.delete_volume_file(path=key)
            return True
        except Exception as e:
            logger.error(f"delete_file: failed to delete file {file_id}, e={e}")
            return False

    async def save_to_volume(
        self,
        file_bytes: bytes,
        path: str,
    ):
        """
        Save file to volume
        :param file_bytes: the content of the file
        :param path: the path of the file
        """
        if self._volume:
            # create directory if not exists
            file_path = f"{self._volume}/{path}"
            file_dir = file_path[: file_path.rfind("/")]
            if not await aiofiles.os.path.exists(file_dir):
                await aiofiles.os.makedirs(file_dir)
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(file_bytes)
        else:
            raise ValueError("PATH_TO_VOLUME is not set")

    async def check_volume_file_exists(
        self,
        path: str,
    ):
        """
        Check if file exists
        :param path: the path of the file
        """
        try:
            async with aiofiles.open(f"{self._volume}/{path}", mode="r"):
                return
        except FileNotFoundError:
            raise FileNotFoundError(f"File {path} not found")

    async def read_volume_file(
        self,
        path: str,
    ) -> bytes:
        """
        Read file from volume
        :param path: the path of the file
        """
        if self._volume:
            async with aiofiles.open(f"{self._volume}/{path}", mode="rb") as f:
                return await f.read()
        else:
            raise ValueError("PATH_TO_VOLUME is not set")

    async def delete_volume_file(
        self,
        path: str,
    ):
        """
        Delete file from volume
        :param path: the path of the file
        """
        if self._volume:
            await aiofiles.os.remove(f"{self._volume}/{path}")
        else:
            raise ValueError("PATH_TO_VOLUME is not set")

    async def get_volume_file_metadata(
        self,
        path: str,
    ) -> Dict:
        """
        Get file metadata from volume
        :param path: the path of the file
        """
        if self._volume:
            file_size = await aiofiles.os.path.getsize(f"{self._volume}/{path}")
            return {"file_size": file_size}
        raise ValueError("PATH_TO_VOLUME is not set")
