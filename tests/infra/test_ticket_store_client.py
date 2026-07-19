"""Тесты in-process MCP-стора тикетов (TicketStoreClient).

Проверяем загрузку из JSON, три тула и обработку ошибок — на временном файле
и на прямых data=, без сети и подпроцессов.
"""
from __future__ import annotations

import json

import pytest

from infra.ticket_store_client import TicketStoreClient, TicketStoreError


DATA = {
    "users": [
        {"id": "U-100", "name": "Иван", "email": "ivan@ex.com",
         "plan": "Free", "auth_method": "SSO (Google)", "platform": "web"},
        {"id": "U-200", "name": "Мария", "plan": "Business"},
    ],
    "tickets": [
        {"id": "T-1024", "user_id": "U-100", "status": "open", "priority": "high",
         "product_area": "auth", "error_code": "403",
         "subject": "Не могу войти через Google",
         "messages": [{"author": "user", "text": "ошибка 403 при входе"}]},
        {"id": "T-1042", "user_id": "U-200", "status": "pending",
         "product_area": "billing", "subject": "Годовая оплата",
         "messages": [{"author": "user", "text": "хочу годовую подписку"}]},
    ],
}


def _client(data=DATA):
    c = TicketStoreClient(data=data)
    c.start()
    return c


def test_lists_three_tools():
    names = {t.name for t in _client().list_tools()}
    assert names == {"get_ticket", "get_user", "search_tickets"}


def test_tools_are_namespaced_to_server():
    tools = _client().list_tools()
    assert all(t.server_id == "support" for t in tools)
    assert all(t.qualified_name.startswith("support__") for t in tools)


def test_get_ticket_includes_author_profile_and_messages():
    res = _client().call_tool("get_ticket", {"ticket_id": "T-1024"})
    assert not res.is_error
    # Ключевой контекст для ответа: тариф Free + SSO объясняют 403.
    assert "T-1024" in res.text
    assert "Free" in res.text and "SSO" in res.text
    assert "403" in res.text
    assert "ошибка 403 при входе" in res.text


def test_get_ticket_is_case_insensitive():
    res = _client().call_tool("get_ticket", {"ticket_id": "t-1024"})
    assert not res.is_error
    assert "T-1024" in res.text


def test_get_ticket_unknown_is_error_and_lists_available():
    res = _client().call_tool("get_ticket", {"ticket_id": "T-9999"})
    assert res.is_error
    assert "T-1024" in res.text  # подсказка со списком доступных


def test_get_user_lists_their_tickets():
    res = _client().call_tool("get_user", {"user_id": "U-100"})
    assert not res.is_error
    assert "Иван" in res.text
    assert "T-1024" in res.text


def test_search_by_product_area():
    res = _client().call_tool("search_tickets", {"product_area": "auth"})
    assert "T-1024" in res.text
    assert "T-1042" not in res.text


def test_search_by_status_and_query():
    res = _client().call_tool("search_tickets", {"status": "pending", "query": "годовую"})
    assert "T-1042" in res.text
    assert "T-1024" not in res.text


def test_search_no_match_is_not_error():
    res = _client().call_tool("search_tickets", {"query": "нет-такого"})
    assert not res.is_error
    assert "не найдено" in res.text.lower()


def test_missing_required_arg_is_error():
    res = _client().call_tool("get_ticket", {})
    assert res.is_error


def test_unknown_tool_is_error():
    res = _client().call_tool("nope", {})
    assert res.is_error


def test_start_reads_from_file(tmp_path):
    p = tmp_path / "tickets.json"
    p.write_text(json.dumps(DATA, ensure_ascii=False), encoding="utf-8")
    c = TicketStoreClient(path=str(p))
    c.start()
    assert not c.call_tool("get_ticket", {"ticket_id": "T-1024"}).is_error


def test_start_missing_file_raises(tmp_path):
    c = TicketStoreClient(path=str(tmp_path / "nope.json"))
    with pytest.raises(TicketStoreError):
        c.start()


def test_start_bad_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    c = TicketStoreClient(path=str(p))
    with pytest.raises(TicketStoreError):
        c.start()
