"""Custom Django authentication backend"""
import abc

from boto3.exceptions import Boto3Error
from botocore.exceptions import ClientError
from django import VERSION as DJANGO_VERSION
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.utils.six import iteritems

from cognito import Cognito
from .utils import cognito_to_dict


class CognitoUser(Cognito):
    user_class = get_user_model()
    # Mapping of Cognito User attribute name to Django User attribute name
    COGNITO_ATTR_MAPPING = getattr(settings, 'COGNITO_ATTR_MAPPING',
                                   {
                                       'email': 'email',
                                       'given_name': 'first_name',
                                       'family_name': 'last_name',
                                   }
                                   )

    def get_user_obj(self,username=None,attribute_list=[],metadata={}):
        user_attrs = cognito_to_dict(attribute_list,CognitoUser.COGNITO_ATTR_MAPPING)
        api_key = user_attrs.pop('api_key', None)
        api_key_id = user_attrs.pop('api_key_id', None)
        if getattr(settings, 'CREATE_UNKNOWN_USERS', True):
            user, created = self.user_class.objects.update_or_create(
                username=username,
                defaults=user_attrs)
        else:
            try:
                user = self.user_class.objects.get(username=username)
                for k, v in iteritems(user_attrs):
                    setattr(user, k, v)
                user.save()
            except self.user_class.DoesNotExist:
                user = None
        if user:
            setattr(user, 'api_key', api_key)
            setattr(user, 'api_key_id', api_key_id)
        return user


class AbstractCognitoBackend(ModelBackend):
    __metaclass__ = abc.ABCMeta

    supports_inactive_user = False

    INACTIVE_USER_STATUS = ['ARCHIVED', 'COMPROMISED', 'UNKNOWN']

    UNAUTHORIZED_ERROR_CODE = 'NotAuthorizedException'

    USER_NOT_FOUND_ERROR_CODE = 'UserNotFoundException'

    COGNITO_USER_CLASS = CognitoUser

    @abc.abstractmethod
    def authenticate(self, username=None, password=None):
        """
        Authenticate a Cognito User
        :param username: Cognito username
        :param password: Cognito password
        :return: returns User instance of AUTH_USER_MODEL or None
        """
        cognito_user = CognitoUser(
            settings.COGNITO_USER_POOL_ID,settings.COGNITO_APP_ID,
            username=username)
        try:
            cognito_user.authenticate(password)
        except (Boto3Error, ClientError) as e:
            return self.handle_error_response(e)
        user = cognito_user.get_user()
        if user:
            setattr(user, 'access_token', cognito_user.access_token)
            setattr(user, 'id_token', cognito_user.id_token)
            setattr(user, 'refresh_token', cognito_user.refresh_token)
        return user


    def handle_error_response(self, error):
        error_code = error.response['Error']['Code']
        if error_code in [
                AbstractCognitoBackend.UNAUTHORIZED_ERROR_CODE,
                AbstractCognitoBackend.USER_NOT_FOUND_ERROR_CODE
            ]:
            return None
        raise error


if DJANGO_VERSION[1] > 10:
    class CognitoBackend(AbstractCognitoBackend):
        def authenticate(self, request, username=None, password=None):
            """
            Authenticate a Cognito User and store an access, ID and 
            refresh token in the session.
            """
            user = super(CognitoBackend, self).authenticate(
                username=username, password=password)
            if user:
                request.session['ACCESS_TOKEN'] = user.access_token
                request.session['ID_TOKEN'] = user.id_token
                request.session['REFRESH_TOKEN'] = user.refresh_token
                request.session.save()
            return user
else:
    class CognitoBackend(AbstractCognitoBackend):
        def authenticate(self, username=None, password=None):
            """
            Authenticate a Cognito User
            """
            return super(CognitoBackend, self).authenticate(
                username=username, password=password)
