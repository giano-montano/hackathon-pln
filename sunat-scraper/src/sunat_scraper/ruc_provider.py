"""Interfaz para consultar datos de un RUC mediante una API externa.

Los datos dinamicos (estado del RUC, razon social, condicion de habido, deudas,
tramites) NO forman parte del corpus ni de los embeddings: cambian a diario y
volverian obsoletas las respuestas del chatbot. El chatbot debe resolverlos en
tiempo de consulta llamando a un proveedor que implemente `RucProvider`.

Aqui solo se define el contrato y un proveedor simulado para pruebas. La
integracion real se hara despues.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RucProvider(Protocol):
    def get(self, ruc: str) -> dict:
        ...


class MockRucProvider:
    """Proveedor simulado. Devuelve datos fijos; no hace ninguna peticion de red."""

    def __init__(self, responses: dict[str, dict] | None = None):
        self.responses = responses or {}

    def get(self, ruc: str) -> dict:
        if ruc in self.responses:
            return self.responses[ruc]
        return {
            "ruc": ruc,
            "razon_social": "EMPRESA DE PRUEBA S.A.C.",
            "estado": "ACTIVO",
            "condicion": "HABIDO",
            "fuente": "mock",
        }
