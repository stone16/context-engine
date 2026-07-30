from fastapi.testclient import TestClient

from adapters.http.app import SERVICE_UNAVAILABLE_RESPONSE, create_app
from engine.runtime.model_inference import ModelInferenceUnavailable


def test_model_inference_availability_maps_to_existing_generic_503_shape() -> None:
    app = create_app()

    @app.get("/_model-inference-unavailable-test", include_in_schema=False)
    def unavailable() -> None:
        raise ModelInferenceUnavailable

    response = TestClient(app).get("/_model-inference-unavailable-test")

    assert response.status_code == 503
    assert response.json() == SERVICE_UNAVAILABLE_RESPONSE
    assert response.content == b'{"code":"service_unavailable"}'
