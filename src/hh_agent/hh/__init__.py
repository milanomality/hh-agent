"""Интеграция с открытым API hh.ru: поиск и карточки вакансий."""

from .client import HHApiError, HHClient

__all__ = ["HHApiError", "HHClient"]
