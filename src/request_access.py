"""
Lambda function to generate a JWT to grant access to the staging environment.

This lambda is called in response to a POST-event. The POST-data (i.e. body)
should contain a (application/x-www-form-encoded, %-encoded) list of key-value
pairs. The key `exp` is required, and indicates the expiration time (in seconds
since 1970-01-01T00:00:00+00:00) of the requested token. Key `subject` is also
required and is an arbitrary text field (may be empty). All other keys are
interpreted as domain-names to include in the token. Their value is ignored,
which makes it work as expected when the domain names are a set of <input
type="checkbox">'s.
"""
import re
import time
import urllib.parse

import jwt
import attr
import structlog

from utils import get_jwt_secret, validate_login_cookie, NotLoggedInError, main_url, get_domains


structlog.configure(processors=[structlog.processors.JSONRenderer()])


@attr.s(slots=True, auto_attribs=True)
class GenerateJwtRequest:
    expire: int
    subject: str
    domains: set


def validate_request(event: dict) -> GenerateJwtRequest:
    structlog.get_logger().log("Validating request", body=event['body'])
    values = urllib.parse.parse_qs(event['body'], strict_parsing=True)
    structlog.get_logger().log("Decoded body", body=values)

    exp = int(values['exp'][0])
    del values['exp']

    subject = values['subject'][0]
    del values['subject']

    if exp > int(time.time()) + 180*24*60*60:
        raise ValueError("Requested validity too long. Refusing")

    domains = set(values.keys())
    for domain in domains:
        if not re.match(r'^[a-zA-Z0-9.-]+$', domain):
            raise ValueError(f"`{domain}` does not look like a domain name")

    if not domains.issubset(get_domains()):
        raise ValueError("Unknown domain requested")

    return GenerateJwtRequest(
        expire=exp,
        subject=subject,
        domains=domains,
    )


def generate_url(url_prefix: str, login_cookie: dict, request: GenerateJwtRequest) -> str:
    token = {
        'iat': int(time.time()),
        'exp': request.expire,
        'domains': list(request.domains),
        'azp': login_cookie['azp'],  # Authorized Party
        'sub': request.subject,  # subject
    }
    structlog.get_logger().log("Issuing JWT", jwt=token)
    token = jwt.encode(
        token,
        get_jwt_secret(),
        algorithm='HS256',
    ).decode('ascii')
    # Token is Base64url encoded, needs no further encoding
    return f"{url_prefix}grant_access?token={token}"


def handler(event, context) -> dict:
    del context  # unused

    domain_prefix = main_url(event)

    try:
        login_cookie = validate_login_cookie(event)  # may raise

        request = validate_request(event)
    except NotLoggedInError as e:
        structlog.get_logger().log("Forbidden", exception=e)
        return {
            'statusCode': 403,
            'body': 'Not logged in',
        }
    except (ValueError, KeyError) as e:
        structlog.get_logger().log("Invalid request", exception=e)
        return {
            'statusCode': 400,
            'body': 'bad request',
        }

    structlog.get_logger().log("Access granted")
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'text/json',
        },
        'body': generate_url(domain_prefix, login_cookie, request),
    }
