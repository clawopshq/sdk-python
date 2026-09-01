import json
import httpx
import pytest
import respx

from clawops._base_client import SyncAPIClient
from clawops.resources.messages import Messages
from clawops.types.message import Message

BASE = "https://api.claw-ops.com"
ACCOUNT = "AC1a2b3c4d"
MESSAGES_PATH = f"/v1/accounts/{ACCOUNT}/messages"

MESSAGE_JSON = {
    "messageId": "MG0123456789abcdef0123456789abcdef",
    "status": "queued", "type": "sms",
    "to": "01012345678", "from": "07052358010",
    "body": "안녕하세요", "direction": "outbound",
    "accountId": "AC1a2b3c4d",
    "dateCreated": "2025-06-01T12:00:00Z", "dateUpdated": None,
}

# 알림톡. 서버가 실제로 주는 모양이다 — type 은 'kakao' 가 아니라 'ata' 이고
# body 에는 템플릿 변수를 치환한 결과가 담긴다.
ATA_JSON = {
    **MESSAGE_JSON,
    "messageId": "MGata0123456789abcdef0123456789ab",
    "type": "ata",
    "status": "sent",
    "body": "홍길동님, 주문이 접수되었습니다.",
}


@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()


@pytest.fixture
def messages(client):
    return Messages(client=client, account_id=ACCOUNT)


class TestMessagesCreate:
    @respx.mock
    def test_create_sms(self, messages):
        respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=MESSAGE_JSON))
        msg = messages.create(to="01012345678", from_="07052358010", body="안녕하세요")
        assert isinstance(msg, Message)
        assert msg.message_id == "MG0123456789abcdef0123456789abcdef"
        assert msg.from_ == "07052358010"
        assert msg.type == "sms"

    @respx.mock
    def test_create_mms(self, messages):
        mms_json = {**MESSAGE_JSON, "type": "mms"}
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=mms_json))
        messages.create(to="010", from_="070", body="사진", type="mms", subject="제목")
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Type"] == "mms"
        assert parsed["Subject"] == "제목"

    @respx.mock
    def test_create_mms_with_media(self, messages):
        mms_json = {
            **MESSAGE_JSON,
            "type": "mms",
            "numMedia": 1,
            "mediaUrl": ["https://example.com/image.jpg"],
        }
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(
            return_value=httpx.Response(201, json=mms_json)
        )
        msg = messages.create(
            to="010", from_="070", body="사진",
            type="mms", subject="제목",
            media_url=["https://example.com/image.jpg"],
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Type"] == "mms"
        assert parsed["MediaUrl"] == ["https://example.com/image.jpg"]
        assert msg.num_media == 1
        assert msg.media_url == ["https://example.com/image.jpg"]


class TestMessagesList:
    @respx.mock
    def test_list_messages(self, messages):
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON], "meta": {"total": 1, "page": 0, "pageSize": 20},
        }))
        page = messages.list()
        items = list(page)
        assert len(items) == 1
        assert isinstance(items[0], Message)

    @respx.mock
    def test_list_with_filters(self, messages):
        route = respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 10},
        }))
        messages.list(type="sms", status="sent", page=0, page_size=10)
        url = str(route.calls[0].request.url)
        assert "type=sms" in url
        assert "status=sent" in url
        assert "pageSize=10" in url


class TestMessagesGet:
    @respx.mock
    def test_get_message(self, messages):
        mid = "MG0123456789abcdef0123456789abcdef"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=MESSAGE_JSON))
        msg = messages.get(mid)
        assert msg.message_id == mid
        assert msg.body == "안녕하세요"


class TestAtaResponseParsing:
    """회귀: 알림톡 응답이 SDK 를 던지게 하던 것.

    서버는 알림톡을 `type: "ata"` 로 주는데 모델이 `"kakao"` 를 기다리고 있었다.
    `"kakao"` 는 서버가 한 번도 보낸 적 없는 값이다. 그래서 콘솔로 알림톡을 한 번이라도
    보낸 계정은 문자 조회까지 통째로 막혔다 — 목록은 페이지에 알림톡 한 건만 섞여도
    전부 실패했다.
    """

    @respx.mock
    def test_get_ata_does_not_raise(self, messages):
        mid = ATA_JSON["messageId"]
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=ATA_JSON))
        msg = messages.get(mid)
        assert msg.type == "ata"
        assert msg.body == "홍길동님, 주문이 접수되었습니다."

    @respx.mock
    def test_list_with_ata_mixed_in_does_not_raise(self, messages):
        """알림톡 한 건이 섞였다고 목록 전체가 죽지 않는다.

        이 경로는 특히 나빴다. 아이템 검증이 클라이언트의 try 밖(resources/messages.py)
        에서 돌아 raw pydantic.ValidationError 가 그대로 샜다 — ClawOpsError 를 상속하지
        않으므로 사용자의 except ClawOpsError 에도 걸리지 않았다.
        """
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON, ATA_JSON], "meta": {"total": 2, "page": 0, "pageSize": 20},
        }))
        types = [m.type for m in messages.list()]
        assert types == ["sms", "ata"]

    @respx.mock
    def test_unknown_type_does_not_raise(self, messages):
        """서버가 유형을 새로 늘려도 던지지 않는다 — 같은 사고를 두 번 겪지 않기 위해."""
        mid = "MGfuture00000000000000000000000000"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(
            return_value=httpx.Response(200, json={**MESSAGE_JSON, "messageId": mid, "type": "rcs"})
        )
        assert messages.get(mid).type == "rcs"

    @respx.mock
    def test_unknown_status_does_not_raise(self, messages):
        mid = "MGfuture00000000000000000000000001"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(
            return_value=httpx.Response(200, json={**MESSAGE_JSON, "messageId": mid, "status": "pending_review"})
        )
        assert messages.get(mid).status == "pending_review"


# --- Async Tests ---

from clawops._base_client import AsyncAPIClient
from clawops.resources.messages import AsyncMessages


@pytest.fixture
def async_client():
    c = AsyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c


@pytest.fixture
def async_messages(async_client):
    return AsyncMessages(client=async_client, account_id=ACCOUNT)


class TestAsyncMessagesCreate:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_sms(self, async_messages):
        respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=MESSAGE_JSON))
        msg = await async_messages.create(to="01012345678", from_="07052358010", body="안녕하세요")
        assert isinstance(msg, Message)
        assert msg.message_id == "MG0123456789abcdef0123456789abcdef"

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_mms(self, async_messages):
        mms_json = {**MESSAGE_JSON, "type": "mms"}
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=mms_json))
        await async_messages.create(to="010", from_="070", body="사진", type="mms", subject="제목")
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Type"] == "mms"


class TestAsyncMessagesList:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_messages(self, async_messages):
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON], "meta": {"total": 1, "page": 0, "pageSize": 20},
        }))
        page = await async_messages.list()
        items = list(page)
        assert len(items) == 1
        assert isinstance(items[0], Message)

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_with_filters(self, async_messages):
        route = respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 10},
        }))
        await async_messages.list(type="sms", status="sent", page=0, page_size=10)
        url = str(route.calls[0].request.url)
        assert "type=sms" in url
        assert "status=sent" in url


class TestAsyncMessagesGet:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_message(self, async_messages):
        mid = "MG0123456789abcdef0123456789abcdef"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=MESSAGE_JSON))
        msg = await async_messages.get(mid)
        assert msg.message_id == mid


class TestAsyncAtaResponseParsing:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_ata_does_not_raise(self, async_messages):
        mid = ATA_JSON["messageId"]
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=ATA_JSON))
        msg = await async_messages.get(mid)
        assert msg.type == "ata"

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_with_ata_mixed_in_does_not_raise(self, async_messages):
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON, ATA_JSON], "meta": {"total": 2, "page": 0, "pageSize": 20},
        }))
        page = await async_messages.list()
        assert [m.type for m in page] == ["sms", "ata"]
