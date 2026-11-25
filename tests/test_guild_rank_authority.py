import pytest

import guild_rank_authority as ga


def guild(**kwargs):
    g = ga.GuildRankAuthority(**kwargs)
    g.found_guild("master")
    g.set_rank_permissions(1, ["chat"])
    g.set_rank_permissions(2, ["invite"])
    g.set_rank_permissions(3, ["kick"])
    g.set_rank_permissions(4, ["promote"])
    return g


def test_rank_permissions_are_inherited():
    g = guild()
    g.add_member("officer", 3)
    g.add_member("rookie", 1)
    assert g.check("master", "promote") is True
    assert g.check("master", "chat") is True
    assert g.check("officer", "kick") is True
    assert g.check("officer", "promote") is False
    assert g.check("rookie", "chat") is True
    assert g.check("rookie", "invite") is False


def test_member_errors_are_reported():
    g = guild()
    with pytest.raises(ga.GuildError) as ei:
        g.check("ghost", "chat")
    assert ei.value.code == "GUILD_NO_MEMBER"
    g.add_member("v", 1)
    with pytest.raises(ga.GuildError) as ei2:
        g.add_member("v", 2)
    assert ei2.value.code == "GUILD_DUPLICATE"
    with pytest.raises(ga.GuildError) as ei3:
        g.add_member("w", 5)
    assert ei3.value.code == "GUILD_BAD_RANK"


def test_permission_table_change_is_immediate():
    g = guild()
    g.add_member("v", 1)
    assert g.check("v", "chat") is True
    g.set_rank_permissions(1, [])
    assert g.check("v", "chat") is False
    g.set_rank_permissions(2, ["invite", "chat"])
    g.add_member("v2", 2)
    assert g.check("v2", "chat") is True
    assert g.check("v2", "invite") is True
    with pytest.raises(ga.GuildError) as ei:
        g.set_rank_permissions(9, ["x"])
    assert ei.value.code == "GUILD_BAD_RANK"


def test_set_member_rank_requires_higher_authority():
    g = guild()
    g.add_member("a", 2)
    g.add_member("b", 1)
    out = g.set_member_rank("master", "b", 3)
    assert out["member"] == "b"
    assert out["rank"] == 3
    assert g.check("b", "kick") is True
    with pytest.raises(ga.GuildError) as ei:
        g.set_member_rank("a", "master", 1)
    assert ei.value.code == "GUILD_RANK_TOO_LOW"
    with pytest.raises(ga.GuildError) as ei2:
        g.set_member_rank("master", "b", 4)
    assert ei2.value.code == "GUILD_RANK_TOO_LOW"


def test_temp_grant_lets_target_act():
    g = guild()
    g.add_member("t", 1)
    rec = g.grant_temp("master", "t", ["promote"], 1000, now=10)
    assert rec["granted"] == ["promote"]
    assert rec["expires_at"] == 1000
    assert g.check("t", "promote", at=20) is True
    assert g.check("t", "promote", at=999) is True
    assert g.check("t", "promote", at=1000) is False


def test_temp_grant_denied_without_permission():
    g = guild()
    g.add_member("t", 1)
    with pytest.raises(ga.GuildError) as ei:
        g.grant_temp("master", "t", ["unknown_perm"], 100, now=0)
    assert ei.value.code == "GUILD_DENIED"
    with pytest.raises(ga.GuildError) as ei2:
        g.grant_temp("t", "t", ["promote"], 100, now=0)
    assert ei2.value.code == "GUILD_DENIED"


def test_expire_grants_reclaims_and_counts():
    g = guild()
    g.add_member("t", 1)
    g.grant_temp("master", "t", ["promote"], 100, now=0)
    assert g.expire_grants(50) == 0
    assert g.check("t", "promote", at=60) is True
    assert g.expire_grants(100) == 1
    assert g.check("t", "promote", at=60) is False
    assert g.active_grants("t", at=60) == []


def test_revoke_requires_grantor_or_master():
    g = guild()
    g.add_member("t", 1)
    g.add_member("other", 1)
    rec = g.grant_temp("master", "t", ["promote"], 1000, now=0)
    with pytest.raises(ga.GuildError) as ei:
        g.revoke("other", rec["grant_id"])
    assert ei.value.code == "GUILD_DENIED"
    assert g.check("t", "promote", at=10) is True
    assert g.revoke("master", rec["grant_id"]) is True
    assert g.check("t", "promote", at=10) is False
    with pytest.raises(ga.GuildError) as ei2:
        g.revoke("master", "g999")
    assert ei2.value.code == "GUILD_NO_GRANT"


def test_grant_rejects_bad_expiry_and_unknown_target():
    g = guild()
    g.add_member("t", 1)
    with pytest.raises(ga.GuildError) as ei:
        g.grant_temp("master", "t", ["promote"], 10, now=10)
    assert ei.value.code == "GUILD_BAD_EXPIRY"
    with pytest.raises(ga.GuildError) as ei2:
        g.grant_temp("master", "ghost", ["promote"], 100, now=0)
    assert ei2.value.code == "GUILD_NO_MEMBER"


def test_active_grants_sorted_by_grant_id():
    g = guild()
    g.add_member("t", 1)
    r1 = g.grant_temp("master", "t", ["promote"], 500, now=0)
    r2 = g.grant_temp("master", "t", ["chat"], 900, now=0)
    grants = g.active_grants("t", at=50)
    assert [x.grant_id for x in grants] == [r1["grant_id"], r2["grant_id"]]
    assert [x.permissions for x in grants] == [("promote",), ("chat",)]
    assert grants[0].grantor == "master"
    assert grants[0].expires_at == 500


def test_audit_log_is_ordered_and_read_only():
    g = guild()
    g.add_member("t", 1)
    g.grant_temp("master", "t", ["promote"], 100, now=0)
    log = g.audit_log()
    assert [e["seq"] for e in log] == [1, 2, 3]
    assert [e["action"] for e in log] == [
        "guild_founded", "member_added", "temp_granted"]
    log.append({"seq": 99, "action": "tamper"})
    assert len(g.audit_log()) == 3
    assert g.audit_log()[2]["action"] == "temp_granted"


def test_grant_capacity_is_enforced():
    g = ga.GuildRankAuthority(max_temp_grants=1)
    g.found_guild("master")
    g.set_rank_permissions(4, ["promote"])
    g.add_member("t", 1)
    r1 = g.grant_temp("master", "t", ["promote"], 500, now=0)
    with pytest.raises(ga.GuildError) as ei:
        g.grant_temp("master", "t", ["promote"], 900, now=0)
    assert ei.value.code == "GUILD_GRANT_CAP"
    assert g.revoke("master", r1["grant_id"]) is True
    r2 = g.grant_temp("master", "t", ["promote"], 900, now=0)
    assert r2["granted"] == ["promote"]


def test_snapshot_restore_roundtrip():
    g = guild()
    g.add_member("t", 1)
    rec = g.grant_temp("master", "t", ["promote"], 1000, now=0)
    snap = g.snapshot()
    assert g.check("t", "promote", at=10) is True
    g.revoke("master", rec["grant_id"])
    assert g.check("t", "promote", at=10) is False
    g.restore(snap)
    assert g.check("t", "promote", at=10) is True
    assert g.snapshot() == snap


def test_restore_rejects_malformed_state():
    g = guild()
    g.add_member("t", 1)
    with pytest.raises(ga.GuildError) as ei:
        g.restore({"members": "bad"})
    assert ei.value.code == "GUILD_SNAPSHOT_INVALID"
    assert g.check("t", "chat") is True


def test_transfer_master_moves_authority():
    g = guild()
    g.add_member("n", 2)
    out = g.transfer_master("master", "n", now=5)
    assert out["master"] == "n"
    assert out["previous_master"] == "master"
    assert g.check("n", "promote") is True
    assert g.check("n", "kick") is True
    assert g.check("master", "promote") is False
    with pytest.raises(ga.GuildError) as ei:
        g.transfer_master("master", "n", now=6)
    assert ei.value.code == "GUILD_DENIED"
