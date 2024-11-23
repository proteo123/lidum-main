import base64
from datetime import datetime

from pydantic import Field, BaseModel, field_validator
from tonsdk.utils import Address, InvalidAddressError


class DropperPriceParams(BaseModel):
    nfts_cnt: int = Field(..., ge=0)

    @field_validator("nfts_cnt", mode="before")
    def convert_nfts_cnt(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("nfts_cnt must be convertible to integer")


class CreateDropParams(BaseModel):
    telegram_id: int
    start_date: str
    end_date: str
    prizes: str
    price: int | float = Field(..., ge=0)

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")

    @field_validator("start_date", "end_date", mode="before")
    def validate_datetime_format(cls, value):

        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
            return value

        except ValueError:

            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M")
                return value

            except ValueError:
                raise ValueError("Date must be in ISO format 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DDTHH:MM:SS'")


class ChannelAvatarParams(BaseModel):
    channel_url: str = Field(..., pattern=r"^(https://t\.me/[A-Za-z0-9_]+|@[A-Za-z0-9_]+)$")


class CheckPasswordParams(BaseModel):
    event_id: str
    password: str


class EventInfoParams(BaseModel):
    event_id: str


class AddVisitedChannelParams(BaseModel):
    telegram_id: int
    channel: str = Field(..., pattern=r"^(https://t\.me/[A-Za-z0-9_]+|@[A-Za-z0-9_]+)$")

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")


class IsUserSubscribedParams(BaseModel):
    telegram_id: int
    channel: str = Field(..., pattern=r"^(https://t\.me/[A-Za-z0-9_]+|@[A-Za-z0-9_]+)$")

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")


class UserInfoParams(BaseModel):
    telegram_id: int
    username: str = Field(..., max_length=32)
    event_id: str

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")


class GetPriceParams(BaseModel):
    telegram_id: int
    collection_images_cnt: int = Field(..., ge=0)

    @field_validator("telegram_id", "collection_images_cnt", mode="before")
    def convert_to_int(cls, value, field):

        try:
            return int(value)

        except ValueError:
            raise ValueError(f"{field.name} must be convertible to integer")


class AddTransactionParams(BaseModel):
    transaction_hash: str
    wallet_address: str
    amount: float | int = Field(..., ge=0)
    event_id: str

    @field_validator("wallet_address", mode="before")
    def validate_wallet_address(cls, value):

        try:
            Address(value)
            return value

        except InvalidAddressError:
            raise ValueError("wallet_address is an invalid address")


class TransactionStatusParams(BaseModel):
    transaction_id: int = Field(..., ge=0)

    @field_validator("transaction_id", mode="before")
    def convert_to_int(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("transaction_id must be convertible to integer")


class AuthorInfoParams(BaseModel):
    telegram_id: int
    username: str = Field(..., max_length=32)

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")


class MakePostParams(BaseModel):
    qrcode: str
    description: str
    button: str
    telegram_id: int
    button_url: str

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")

    @field_validator("qrcode", mode="before")
    def validate_base64_qrcode(cls, value):

        if not value.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")):
            raise ValueError("qrcode must start with a valid base64 prefix,"
                             "e.g., 'data:image/jpeg;base64,' or 'data:image/png;base64,'")

        base64_data = value.split(",")[1]

        try:
            base64.b64decode(base64_data, validate=True)
            return value

        except (ValueError, base64.binascii.Error):
            raise ValueError("qrcode must be a valid base64-encoded string")


class CreateEventParams(BaseModel):
    telegram_id: int
    wallet_address: str
    event_name: str = Field(..., max_length=64)
    event_description: str
    collection_name: str = Field(..., max_length=16)
    nfts_cnt: int = Field(..., ge=0)
    image_name: str
    image: str
    start_date: str
    end_date: str
    password: str = Field(..., max_length=64)
    subscriptions: str = Field(..., pattern=r"^(@\w+)(,@\w+)*$")
    price: float | int = Field(..., ge=0)
    user_timezone: int = Field(..., ge=-11, le=12)
    event_id: str | None = Field(default=None)
    invite: int = Field(default=0, ge=0)

    @field_validator("telegram_id", "nfts_cnt", "user_timezone", "invite", mode="before")
    def convert_to_int(cls, value, field):

        try:
            return int(value)

        except ValueError:
            raise ValueError(f"{field.name} must be convertible to integer")

    @field_validator("wallet_address", mode="before")
    def validate_wallet_address(cls, value):

        try:
            Address(value)
            return value

        except InvalidAddressError:
            raise ValueError("wallet_address is an invalid address")

    @field_validator("image", mode="before")
    def validate_base64_image(cls, value):

        if not value.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")):
            raise ValueError("image must start with a valid base64 prefix,"
                             "e.g., 'data:image/jpeg;base64,' or 'data:image/png;base64,'")

        base64_data = value.split(",")[1]

        try:
            base64.b64decode(base64_data, validate=True)
            return value

        except (ValueError, base64.binascii.Error):
            raise ValueError("image must be a valid base64-encoded string")

    @field_validator("start_date", "end_date", mode="before")
    def validate_datetime_format(cls, value):

        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
            return value

        except ValueError:

            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M")
                return value

            except ValueError:
                raise ValueError("Date must be in ISO format 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DDTHH:MM:SS'")


class SendNFTParams(BaseModel):
    telegram_id: int
    wallet_address: str
    event_id: str

    @field_validator("telegram_id", mode="before")
    def convert_telegram_id(cls, value):

        try:
            return int(value)

        except ValueError:
            raise ValueError("telegram_id must be convertible to integer")

    @field_validator("wallet_address", mode="before")
    def validate_wallet_address(cls, value):

        try:
            Address(value)
            return value

        except InvalidAddressError:
            raise ValueError("wallet_address is an invalid address")
