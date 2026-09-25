"""Public runtime descriptions must not advertise an application's auth scheme."""

from langgraph_runtime_pg.protocol import capability_document


def test_capabilities_describe_generic_application_auth():
    assert capability_document()["authentication"] == {"production": "application-auth"}
