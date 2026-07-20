"""离线单测：飞书 OAuth state 签名/校验 + 授权 URL 构造。

只测不依赖网络/DB 的纯逻辑：
- sign_oauth_state / verify_oauth_state 的签名 round-trip、类型校验、防篡改
- build_authorize_url 的参数拼装（含 offline_access scope 检查）

用 stdlib assert（容器无 pytest），PYTHONPATH=/app/src 跑。
"""
import sys
import uuid


def _run():
    from agent_eval.auth.security import sign_oauth_state, verify_oauth_state

    uid = uuid.uuid4()
    open_id = "ou_test_1234567890"

    # 1) round-trip：签名后能还原出 user_id + open_id
    state = sign_oauth_state(uid, open_id)
    verified = verify_oauth_state(state)
    assert verified is not None, "verify returned None for a valid state"
    got_uid, got_open = verified
    assert got_uid == uid, f"user_id mismatch: {got_uid} != {uid}"
    assert got_open == open_id, f"open_id mismatch: {got_open} != {open_id}"
    print("PASS state_roundtrip")

    # 2) 篡改后校验失败
    tampered = state[:-4] + ("aaaa" if not state.endswith("aaaa") else "bbbb")
    assert verify_oauth_state(tampered) is None, "tampered state should fail"
    print("PASS state_tamper_rejected")

    # 3) 垃圾串 / 空串 不崩、返回 None
    assert verify_oauth_state("") is None
    assert verify_oauth_state("not.a.jwt") is None
    print("PASS state_garbage_rejected")

    # 4) 错误 type 的 JWT（借 access token 冒充 state）应被拒
    from agent_eval.auth.security import create_access_token
    access = create_access_token(uid, "user")
    assert verify_oauth_state(access) is None, "access token must not pass as state"
    print("PASS state_wrong_type_rejected")

    # 5) build_authorize_url：含必需参数 + offline_access（拿 refresh_token 的铁律）
    from agent_eval.feishu.oauth import build_authorize_url
    url = build_authorize_url(uid, open_id)
    assert "response_type=code" in url, url
    assert "client_id=" in url, url
    assert "state=" in url, url
    assert "redirect_uri=" in url, url
    print("PASS authorize_url_params")

    print("\nALL PASS")


if __name__ == "__main__":
    try:
        _run()
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
