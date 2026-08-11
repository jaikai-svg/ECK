from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from eck.app import Application


def get_application(request: Request) -> Application:
    return cast(Application, request.app.state.application)


AppDependency = Annotated[Application, Depends(get_application)]

