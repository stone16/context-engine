"""FastAPI server-rendered UI routes over the public HTTP seam."""

from __future__ import annotations

import hmac
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ui.public_http import (
    UI_SESSION_COOKIE,
    UI_SESSION_TTL,
    PublicHttpRefusal,
    issue_ui_session,
    open_citation,
    request_public_json,
    resolve_query,
)
from ui.views import (
    PublicDocumentInvalid,
    article_view,
    ask_view,
    hit_test_view,
    import_preview_view,
    overview_view,
    profiles_view,
    verify_citation_lineage,
)

UI_ROOT: Final = Path(__file__).resolve().parent
MAX_FORM_BYTES: Final = 8_192
MAX_QUERY_CHARACTERS: Final = 2_000
UI_BUILD_IDENTIFIER: Final = distribution_version("context-engine")
_UI_PATHS: Final = frozenset(
    {
        "/ui",
        "/ui/articles",
        "/ui/ask",
        "/ui/feedback",
        "/ui/hit-test",
        "/ui/import",
        "/ui/profiles",
    }
)
templates = Jinja2Templates(directory=UI_ROOT / "templates")


def _html(
    request: Request,
    template: str,
    context: dict[str, object],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context={**context, "build_identifier": UI_BUILD_IDENTIFIER},
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _refusal(
    request: Request,
    *,
    category: str,
    status_code: int,
    query: str = "",
    submitted: tuple[tuple[str, str], ...] = (),
    active_page: str = "hit-test",
    return_path: str = "/ui/hit-test",
) -> HTMLResponse:
    safe_submission = submitted
    if query:
        safe_submission = (
            ("Question" if active_page == "ask" else "Query", query),
        )
    return _html(
        request,
        "refusal.html",
        {
            "active_page": active_page,
            "category": category,
            "submitted": safe_submission,
            "title": "Request refused",
            "return_label": (
                "Authenticate"
                if category == "session_unavailable"
                else "Return to form"
            ),
            "return_path": (
                "/ui/login" if category == "session_unavailable" else return_path
            ),
        },
        status_code=status_code,
    )


def _safe_submission(
    *fields: tuple[str, str | None],
) -> tuple[tuple[str, str], ...]:
    return tuple((label, value) for label, value in fields if value is not None)


async def _session_refusal(
    request: Request,
    *,
    bearer_token: str | None,
    active_page: str,
    return_path: str,
) -> HTMLResponse | None:
    outcome = await request_public_json(
        request,
        bearer_token=bearer_token,
        method="GET",
        path="/v0/ui/session",
    )
    if not isinstance(outcome, PublicHttpRefusal):
        return None
    return _refusal(
        request,
        category=outcome.category,
        status_code=outcome.status_code,
        active_page=active_page,
        return_path=return_path,
    )


async def _query_form(request: Request) -> str | None:
    fields = await _urlencoded_form(request, maximum_fields=1)
    if fields is None:
        return None
    values = fields.get("query")
    if (
        len(fields) != 1
        or values is None
        or not values
        or values.isspace()
        or len(values) > MAX_QUERY_CHARACTERS
    ):
        return None
    return values


async def _urlencoded_form(
    request: Request,
    *,
    maximum_fields: int,
) -> dict[str, str] | None:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        return None
    if content_types[0].partition(";")[0].strip().casefold() != (
        "application/x-www-form-urlencoded"
    ):
        return None
    body = await request.body()
    if len(body) > MAX_FORM_BYTES:
        return None
    try:
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=maximum_fields,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    if len(fields) > maximum_fields or any(
        len(values) != 1 for values in fields.values()
    ):
        return None
    return {name: values[0] for name, values in fields.items()}


def install_ui(app: FastAPI, *, bearer_token: str | None) -> None:
    """Install the presentation into the existing API application process."""

    if bearer_token is not None and (
        type(bearer_token) is not str or not bearer_token or bearer_token.isspace()
    ):
        raise ValueError("UI bearer token is invalid")
    app.mount(
        "/ui-static",
        StaticFiles(directory=UI_ROOT / "static"),
        name="ui-static",
    )

    @app.get("/ui/login", include_in_schema=False, response_class=HTMLResponse)
    async def login_form(request: Request) -> HTMLResponse:
        return _html(
            request,
            "login.html",
            {
                "active_page": "",
                "next_path": "/ui",
                "title": "Authenticate",
            },
        )

    @app.post("/ui/login", include_in_schema=False, response_model=None)
    async def login(request: Request) -> HTMLResponse | RedirectResponse:
        fields = await _urlencoded_form(request, maximum_fields=2)
        credential = None if fields is None else fields.get("credential")
        next_path = "/ui" if fields is None else fields.get("next", "/ui")
        if (
            fields is None
            or set(fields) != {"credential", "next"}
            or credential is None
            or bearer_token is None
            or not hmac.compare_digest(credential, bearer_token)
            or next_path not in _UI_PATHS
        ):
            return _refusal(
                request,
                category="session_unavailable",
                status_code=401,
                active_page="",
                return_path="/ui/login",
            )
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            UI_SESSION_COOKIE,
            issue_ui_session(bearer_token),
            httponly=True,
            max_age=int(UI_SESSION_TTL.total_seconds()),
            path="/ui",
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/ui/logout", include_in_schema=False)
    async def logout(request: Request) -> RedirectResponse:
        del request
        response = RedirectResponse("/ui/login", status_code=303)
        response.delete_cookie(UI_SESSION_COOKIE, path="/ui")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/ui", include_in_schema=False, response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        refusal = await _session_refusal(
            request,
            bearer_token=bearer_token,
            active_page="overview",
            return_path="/ui",
        )
        if refusal is not None:
            return refusal
        return _html(
            request,
            "overview.html",
            {
                "active_page": "overview",
                "current": None,
                "title": "Operational overview",
            },
        )

    @app.post("/ui/overview", include_in_schema=False, response_class=HTMLResponse)
    async def load_overview(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=1)
        control_credential = (
            None if fields is None else fields.get("controlCredential")
        )
        if (
            fields is None
            or set(fields) != {"controlCredential"}
            or control_credential is None
            or not control_credential
            or control_credential.isspace()
            or len(control_credential) > 4096
        ):
            return _refusal(
                request,
                category="control_authority_unavailable",
                status_code=401,
                active_page="overview",
                return_path="/ui",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="GET",
            path="/v0/ui/overview",
            control_credential=control_credential,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                active_page="overview",
                return_path="/ui",
            )
        try:
            current = overview_view(outcome)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="operational_projection_unavailable",
                status_code=503,
                active_page="overview",
                return_path="/ui",
            )
        return _html(
            request,
            "overview.html",
            {
                "active_page": "overview",
                "current": current,
                "title": "Operational overview",
            },
        )

    @app.get("/ui/import", include_in_schema=False, response_class=HTMLResponse)
    async def import_form(request: Request) -> HTMLResponse:
        refusal = await _session_refusal(
            request,
            bearer_token=bearer_token,
            active_page="import",
            return_path="/ui/import",
        )
        if refusal is not None:
            return refusal
        return _html(
            request,
            "import.html",
            {
                "active_page": "import",
                "preview": None,
                "receipt": None,
                "title": "Import Markdown",
            },
        )

    @app.post(
        "/ui/import/preview",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def preview_import(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=3)
        source_ref = None if fields is None else fields.get("sourceRef")
        path = None if fields is None else fields.get("path")
        control_credential = (
            None if fields is None else fields.get("controlCredential")
        )
        submitted = _safe_submission(
            ("Source", source_ref),
            ("Path", path),
        )
        if (
            fields is None
            or set(fields) != {"sourceRef", "path", "controlCredential"}
            or source_ref is None
            or not source_ref
            or len(source_ref) > 36
            or path is None
            or not path
            or path.isspace()
            or len(path) > 255
            or control_credential is None
            or not control_credential
            or control_credential.isspace()
            or len(control_credential) > 4096
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                submitted=submitted,
                active_page="import",
                return_path="/ui/import",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="POST",
            path="/v0/ui/import/preview",
            body={"sourceRef": source_ref, "path": path},
            control_credential=control_credential,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                submitted=submitted,
                active_page="import",
                return_path="/ui/import",
            )
        try:
            preview = import_preview_view(outcome)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="import_preview_unavailable",
                status_code=503,
                submitted=submitted,
                active_page="import",
                return_path="/ui/import",
            )
        return _html(
            request,
            "import.html",
            {
                "active_page": "import",
                "preview": preview,
                "receipt": None,
                "title": "Import Markdown",
            },
        )

    @app.post(
        "/ui/import/confirm",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def confirm_import(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=2)
        preview_token = None if fields is None else fields.get("previewToken")
        control_credential = (
            None if fields is None else fields.get("controlCredential")
        )
        if (
            fields is None
            or set(fields) != {"previewToken", "controlCredential"}
            or preview_token is None
            or not preview_token
            or len(preview_token) > 4096
            or control_credential is None
            or not control_credential
            or control_credential.isspace()
            or len(control_credential) > 4096
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                active_page="import",
                return_path="/ui/import",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="POST",
            path="/v0/ui/import/confirm",
            body={"previewToken": preview_token},
            control_credential=control_credential,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                active_page="import",
                return_path="/ui/import",
            )
        job_ref = outcome.get("jobRef")
        if type(job_ref) is not str or outcome.get("state") != "queued":
            return _refusal(
                request,
                category="import_confirmation_unavailable",
                status_code=503,
                active_page="import",
                return_path="/ui/import",
            )
        return _html(
            request,
            "import.html",
            {
                "active_page": "import",
                "preview": None,
                "receipt": job_ref,
                "title": "Import Markdown",
            },
        )

    @app.get("/ui/articles", include_in_schema=False, response_class=HTMLResponse)
    async def article_form(request: Request) -> HTMLResponse:
        refusal = await _session_refusal(
            request,
            bearer_token=bearer_token,
            active_page="articles",
            return_path="/ui/articles",
        )
        if refusal is not None:
            return refusal
        return _html(
            request,
            "articles.html",
            {
                "active_page": "articles",
                "article": None,
                "change": None,
                "receipt": None,
                "title": "Article visibility",
            },
        )

    @app.post(
        "/ui/articles/view",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def view_article(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=2)
        resource_ref = None if fields is None else fields.get("resourceRef")
        control_credential = (
            None if fields is None else fields.get("controlCredential")
        )
        if (
            fields is None
            or set(fields) != {"resourceRef", "controlCredential"}
            or resource_ref is None
            or not resource_ref
            or resource_ref.isspace()
            or len(resource_ref) > 512
            or control_credential is None
            or not control_credential
            or control_credential.isspace()
            or len(control_credential) > 4096
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                active_page="articles",
                return_path="/ui/articles",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="POST",
            path="/v0/ui/articles/view",
            body={"resourceRef": resource_ref},
            control_credential=control_credential,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                active_page="articles",
                return_path="/ui/articles",
            )
        try:
            current = article_view(outcome)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="article_projection_unavailable",
                status_code=503,
                active_page="articles",
                return_path="/ui/articles",
            )
        return _html(
            request,
            "articles.html",
            {
                "active_page": "articles",
                "article": current,
                "change": None,
                "receipt": None,
                "title": "Article visibility",
            },
        )

    @app.post(
        "/ui/articles/preview",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def preview_article_policy(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=4)
        resource_ref = None if fields is None else fields.get("resourceRef")
        policy_kind = None if fields is None else fields.get("policyKind")
        raw_groups = None if fields is None else fields.get("groupRefs")
        control_credential = (
            None if fields is None else fields.get("controlCredential")
        )
        group_refs = (
            []
            if raw_groups is None or not raw_groups
            else [value.strip() for value in raw_groups.split(",")]
        )
        submitted = _safe_submission(
            ("Article", resource_ref),
            ("Policy", policy_kind),
            ("Groups", ", ".join(group_refs) if group_refs else None),
        )
        if (
            fields is None
            or set(fields)
            != {"resourceRef", "policyKind", "groupRefs", "controlCredential"}
            or resource_ref is None
            or not resource_ref
            or resource_ref.isspace()
            or len(resource_ref) > 512
            or policy_kind not in {"private", "organization", "groups"}
            or any(not value or len(value) > 256 for value in group_refs)
            or (policy_kind == "groups") != bool(group_refs)
            or control_credential is None
            or not control_credential
            or control_credential.isspace()
            or len(control_credential) > 4096
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                submitted=submitted,
                active_page="articles",
                return_path="/ui/articles",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="POST",
            path="/v0/ui/articles/preview",
            body={
                "resourceRef": resource_ref,
                "policyKind": policy_kind,
                "groupRefs": group_refs,
            },
            control_credential=control_credential,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                submitted=submitted,
                active_page="articles",
                return_path="/ui/articles",
            )
        raw_current = outcome.get("current")
        raw_proposed = outcome.get("proposed")
        preview_token = outcome.get("previewToken")
        try:
            if type(raw_current) is not dict:
                raise PublicDocumentInvalid
            current = article_view(raw_current)
            if (
                type(raw_proposed) is not dict
                or type(preview_token) is not str
                or not preview_token
                or raw_proposed.get("policyKind") != policy_kind
                or raw_proposed.get("groupRefs") != group_refs
            ):
                raise PublicDocumentInvalid
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="article_preview_unavailable",
                status_code=503,
                submitted=submitted,
                active_page="articles",
                return_path="/ui/articles",
            )
        return _html(
            request,
            "articles.html",
            {
                "active_page": "articles",
                "article": current,
                "change": {
                    "group_refs": group_refs,
                    "policy_kind": policy_kind,
                    "preview_token": preview_token,
                },
                "receipt": None,
                "title": "Article visibility",
            },
        )

    @app.post(
        "/ui/articles/confirm",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def confirm_article_policy(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=2)
        preview_token = None if fields is None else fields.get("previewToken")
        control_credential = (
            None if fields is None else fields.get("controlCredential")
        )
        if (
            fields is None
            or set(fields) != {"previewToken", "controlCredential"}
            or preview_token is None
            or not preview_token
            or len(preview_token) > 4096
            or control_credential is None
            or not control_credential
            or control_credential.isspace()
            or len(control_credential) > 4096
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                active_page="articles",
                return_path="/ui/articles",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="POST",
            path="/v0/ui/articles/confirm",
            body={"previewToken": preview_token},
            control_credential=control_credential,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                active_page="articles",
                return_path="/ui/articles",
            )
        policy_epoch = outcome.get("policyEpoch")
        policy_version = outcome.get("policyVersion")
        if (
            type(policy_epoch) is not int
            or type(policy_version) is not int
            or outcome.get("state") != "changed"
        ):
            return _refusal(
                request,
                category="article_confirmation_unavailable",
                status_code=503,
                active_page="articles",
                return_path="/ui/articles",
            )
        return _html(
            request,
            "articles.html",
            {
                "active_page": "articles",
                "article": None,
                "change": None,
                "receipt": {
                    "policy_epoch": policy_epoch,
                    "policy_version": policy_version,
                },
                "title": "Article visibility",
            },
        )

    @app.get("/ui/hit-test", include_in_schema=False, response_class=HTMLResponse)
    async def hit_test_form(request: Request) -> HTMLResponse:
        refusal = await _session_refusal(
            request,
            bearer_token=bearer_token,
            active_page="hit-test",
            return_path="/ui/hit-test",
        )
        if refusal is not None:
            return refusal
        return _html(
            request,
            "hit_test.html",
            {
                "active_page": "hit-test",
                "query": "",
                "result": None,
                "title": "Retrieval Hit Test",
            },
        )

    @app.get("/ui/ask", include_in_schema=False, response_class=HTMLResponse)
    async def ask_form(request: Request) -> HTMLResponse:
        refusal = await _session_refusal(
            request,
            bearer_token=bearer_token,
            active_page="ask",
            return_path="/ui/ask",
        )
        if refusal is not None:
            return refusal
        return _html(
            request,
            "ask.html",
            {
                "active_page": "ask",
                "query": "",
                "result": None,
                "title": "Ask ContextEngine",
            },
        )

    @app.post("/ui/ask", include_in_schema=False, response_class=HTMLResponse)
    async def run_ask(request: Request) -> HTMLResponse:
        query = await _query_form(request)
        if query is None:
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                active_page="ask",
                return_path="/ui/ask",
            )
        outcome = await resolve_query(
            request,
            bearer_token=bearer_token,
            query=query,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                query=query,
                active_page="ask",
                return_path="/ui/ask",
            )
        try:
            pending = ask_view(outcome, query=query)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="citation_lineage_unavailable",
                status_code=503,
                query=query,
                active_page="ask",
                return_path="/ui/ask",
            )
        opened: dict[str, dict[str, object]] = {}
        for hit in pending.hits:
            locator = hit.evidence.citation_open_ref
            if locator is None:
                return _refusal(
                    request,
                    category="citation_unavailable",
                    status_code=503,
                    query=query,
                    active_page="ask",
                    return_path="/ui/ask",
                )
            citation = await open_citation(
                request,
                bearer_token=bearer_token,
                citation_open_ref=locator,
            )
            if isinstance(citation, PublicHttpRefusal):
                return _refusal(
                    request,
                    category="citation_unavailable",
                    status_code=citation.status_code,
                    query=query,
                    active_page="ask",
                    return_path="/ui/ask",
                )
            opened[locator] = citation
        try:
            result = verify_citation_lineage(pending, opened)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="citation_unavailable",
                status_code=503,
                query=query,
                active_page="ask",
                return_path="/ui/ask",
            )
        return _html(
            request,
            "ask.html",
            {
                "active_page": "ask",
                "query": query,
                "result": result,
                "title": "Ask ContextEngine",
            },
        )

    @app.post("/ui/hit-test", include_in_schema=False, response_class=HTMLResponse)
    async def run_hit_test(request: Request) -> HTMLResponse:
        query = await _query_form(request)
        if query is None:
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
            )
        outcome = await resolve_query(
            request,
            bearer_token=bearer_token,
            query=query,
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                query=query,
            )
        try:
            result = hit_test_view(outcome, query=query)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="lineage_unavailable",
                status_code=503,
                query=query,
            )
        return _html(
            request,
            "hit_test.html",
            {
                "active_page": "hit-test",
                "query": query,
                "result": result,
                "title": "Retrieval Hit Test",
            },
        )

    @app.get("/ui/profiles", include_in_schema=False, response_class=HTMLResponse)
    async def profiles(request: Request) -> HTMLResponse:
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="GET",
            path="/v0/ui/profiles",
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                active_page="profiles",
                return_path="/ui/profiles",
            )
        try:
            current = profiles_view(outcome)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="profile_projection_unavailable",
                status_code=503,
                active_page="profiles",
                return_path="/ui/profiles",
            )
        return _html(
            request,
            "profiles.html",
            {
                "active_page": "profiles",
                "current": current,
                "proposed": None,
                "title": "Versioned profiles",
            },
        )

    @app.post("/ui/profiles", include_in_schema=False, response_class=HTMLResponse)
    async def preview_profile_change(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=2)
        profile_ref = None if fields is None else fields.get("profileRef")
        digest = None if fields is None else fields.get("digest")
        submitted = _safe_submission(
            ("Profile", profile_ref),
            ("Digest", digest),
        )
        if (
            fields is None
            or set(fields) != {"profileRef", "digest"}
            or profile_ref is None
            or not profile_ref
            or profile_ref.isspace()
            or len(profile_ref) > 256
            or digest is None
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                submitted=submitted,
                active_page="profiles",
                return_path="/ui/profiles",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="GET",
            path="/v0/ui/profiles",
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                submitted=submitted,
                active_page="profiles",
                return_path="/ui/profiles",
            )
        try:
            current = profiles_view(outcome)
        except PublicDocumentInvalid:
            return _refusal(
                request,
                category="profile_projection_unavailable",
                status_code=503,
                submitted=submitted,
                active_page="profiles",
                return_path="/ui/profiles",
            )
        return _html(
            request,
            "profiles.html",
            {
                "active_page": "profiles",
                "current": current,
                "proposed": {
                    "profile_ref": profile_ref,
                    "digest": digest,
                    "reembed_required": (
                        profile_ref != current.index_profile.profile_ref
                        or digest != current.index_profile.digest
                    ),
                },
                "title": "Versioned profiles",
            },
        )

    @app.get("/ui/feedback", include_in_schema=False, response_class=HTMLResponse)
    async def feedback_form(request: Request) -> HTMLResponse:
        refusal = await _session_refusal(
            request,
            bearer_token=bearer_token,
            active_page="feedback",
            return_path="/ui/feedback",
        )
        if refusal is not None:
            return refusal
        return _html(
            request,
            "feedback.html",
            {
                "active_page": "feedback",
                "receipt": None,
                "title": "Answer feedback",
            },
        )

    @app.post("/ui/feedback", include_in_schema=False, response_class=HTMLResponse)
    async def capture_feedback(request: Request) -> HTMLResponse:
        fields = await _urlencoded_form(request, maximum_fields=3)
        run_ref = None if fields is None else fields.get("runRef")
        rating = None if fields is None else fields.get("rating")
        note_value = None if fields is None else fields.get("note")
        note = note_value if note_value else None
        submitted = _safe_submission(
            ("ContextRun", run_ref),
            ("Rating", rating.replace("_", " ") if rating is not None else None),
        )
        if (
            fields is None
            or set(fields) != {"runRef", "rating", "note"}
            or run_ref is None
            or not run_ref
            or run_ref.isspace()
            or len(run_ref) > 256
            or rating not in {"helpful", "not_helpful"}
            or (note is not None and (note.isspace() or len(note) > 1000))
        ):
            return _refusal(
                request,
                category="invalid_request",
                status_code=422,
                submitted=submitted,
                active_page="feedback",
                return_path="/ui/feedback",
            )
        outcome = await request_public_json(
            request,
            bearer_token=bearer_token,
            method="POST",
            path="/v0/ui/feedback",
            body={"runRef": run_ref, "rating": rating, "note": note},
        )
        if isinstance(outcome, PublicHttpRefusal):
            return _refusal(
                request,
                category=outcome.category,
                status_code=outcome.status_code,
                submitted=submitted,
                active_page="feedback",
                return_path="/ui/feedback",
            )
        feedback_ref = outcome.get("feedbackRef")
        state = outcome.get("state")
        if type(feedback_ref) is not str or state != "recorded":
            return _refusal(
                request,
                category="feedback_unavailable",
                status_code=503,
                submitted=submitted,
                active_page="feedback",
                return_path="/ui/feedback",
            )
        return _html(
            request,
            "feedback.html",
            {
                "active_page": "feedback",
                "receipt": feedback_ref,
                "title": "Answer feedback",
            },
        )
