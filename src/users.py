import http
import os
from datetime import datetime, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWTError
from pydantic import BaseModel, Field
from pydantic import parse_obj_as
from pydantic.class_validators import Optional
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from utils import SECRET_KEY

router = APIRouter(
    prefix="/authenv-service",
    tags=["Users"],
)

security = HTTPBearer()


class UserDetailsOutput(BaseModel):
    username: str
    first_name: str = Field(alias='firstName')
    last_name: str = Field(alias='lastName')
    status: str
    email: str
    phone: str
    street_address: Optional[str] = Field(alias='streetAddress')
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str] = Field(alias='zipCode')


class UserDetailsInput(UserDetailsOutput):
    password: Optional[str] = Field(description='Required for insert, Optional for update')


class UserDetailsRequest(BaseModel):
    user_details: UserDetailsInput


class UserDetailsResponse(BaseModel):
    detail: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_details: UserDetailsOutput


@router.post("/login", response_model=LoginResponse, status_code=http.HTTPStatus.OK)
def login(request: Request, login_request: LoginRequest):
    user_details = __get_user_details(request=request, username=login_request.username, password=login_request.password)
    token = __encode_token(username=login_request.username, source_ip=request.client.host)
    return LoginResponse(token=token, user_details=user_details)


@router.post("/{username}", response_model=UserDetailsResponse, status_code=http.HTTPStatus.OK)
def insert(request: Request, username: str, user_details_request: UserDetailsRequest):
    if not username == user_details_request.user_details.username or not user_details_request.user_details.password:
        raise HTTPException(status_code=http.HTTPStatus.BAD_REQUEST,
                            detail='Invalid Request! Invalid User/Password!! Please try again!!!')

    __insert_user_details(request=request, user_details_input=user_details_request.user_details)
    return UserDetailsResponse(detail='Insert Successful!')


@router.put("/{username}", response_model=UserDetailsResponse, status_code=http.HTTPStatus.OK)
def update(request: Request, username: str, user_details_request: UserDetailsRequest,
           credentials: HTTPAuthorizationCredentials = Depends(security)):
    token_username = __decode_token(credentials)
    if not username == token_username == user_details_request.user_details.username:
        raise HTTPException(status_code=http.HTTPStatus.BAD_REQUEST,
                            detail='Invalid Request! Invalid Username!! Please try again!!!')
    __update_user_details(request=request, user_details_input=user_details_request.user_details)
    return UserDetailsResponse(detail='Update Successful!')


@router.get("/{username}", response_model=LoginResponse, status_code=http.HTTPStatus.OK)
def find(request: Request, username: str, http_auth_credentials: HTTPAuthorizationCredentials = Depends(security)):
    token_username = __decode_token(http_auth_credentials)
    if not username == token_username:
        raise HTTPException(status_code=http.HTTPStatus.BAD_REQUEST,
                            detail='Invalid Request! Invalid User!! Please try again!!!')
    user_details = __find_user_by_username(request=request, username=username)
    return LoginResponse(user_details=user_details, token=http_auth_credentials.credentials)


def __user_details_collection(request: Request):
    mongo_client = request.app.mongo_client
    mongo_database = mongo_client.user_details
    mongo_collection = mongo_database.userdetails
    return mongo_collection


def __find_user_by_username(request, username, is_include_password=False):
    mongo_collection: Collection = __user_details_collection(request)

    try:
        user_details = mongo_collection.find_one({'username': username})
    except PyMongoError as ex:
        raise HTTPException(status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
                            detail={'msg': f'Error retrieving user: {username}', 'errMsg': str(ex)})

    if user_details is None:
        raise HTTPException(status_code=http.HTTPStatus.NOT_FOUND, detail=f'User not found: {username}')

    if is_include_password:
        return parse_obj_as(UserDetailsInput, user_details)
    else:
        return parse_obj_as(UserDetailsOutput, user_details)


def __get_user_details(request, username, password):
    user_details = __find_user_by_username(request=request, username=username, is_include_password=True)

    result = bcrypt.checkpw(password=password.encode('utf-8'),
                            hashed_password=user_details.password.encode('utf-8'))
    if result:
        return parse_obj_as(UserDetailsOutput, user_details)
    else:
        raise HTTPException(status_code=http.HTTPStatus.UNAUTHORIZED, detail=f'User not matched: {username}')


def __insert_user_details(request, user_details_input: UserDetailsInput):
    mongo_collection: Collection = __user_details_collection(request)
    hashed_password = bcrypt.hashpw(user_details_input.password.encode('utf-8'), bcrypt.gensalt())
    user_details_input.password = hashed_password
    try:
        mongo_collection.insert_one(jsonable_encoder(user_details_input))
    except PyMongoError as ex:
        raise HTTPException(status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
                            detail={'msg': f'Error inserting user: {user_details_input.username}', 'errMsg': str(ex)})


def __update_user_details(request, user_details_input: UserDetailsInput):
    mongo_collection: Collection = __user_details_collection(request)

    if user_details_input.password:
        hashed_password = bcrypt.hashpw(user_details_input.password.encode('utf-8'), bcrypt.gensalt())
        user_details_input.password = hashed_password

    try:
        update_result = mongo_collection.update_one({'username': user_details_input.username},
                                                    {"$set": jsonable_encoder(obj=user_details_input,
                                                                              exclude_none=True)})
        if update_result.modified_count == 0:
            raise HTTPException(status_code=http.HTTPStatus.SERVICE_UNAVAILABLE,
                                detail=f'Error updating user: {user_details_input.username}')
    except PyMongoError as ex:
        raise HTTPException(status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
                            detail={'msg': f'Error updating user: {user_details_input.username}', 'errMsg': str(ex)})


def __encode_token(username, source_ip):
    token_claim = {
        'username': username,
        'source_ip': source_ip,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload=token_claim, key=os.getenv(SECRET_KEY), algorithm='HS256')


def __decode_token(http_auth_credentials: HTTPAuthorizationCredentials):
    try:
        token_claims = jwt.decode(jwt=http_auth_credentials.credentials, key=os.getenv(SECRET_KEY), algorithms=['HS256'])
        return token_claims.get('username')
    except PyJWTError as ex:
        raise HTTPException(status_code=http.HTTPStatus.FORBIDDEN,
                            detail={'msg': 'Invalid Credentials!', 'errMsg': str(ex)})
