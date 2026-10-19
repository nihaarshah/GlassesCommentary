import pytest
from fastapi.testclient import TestClient

from conftest import jpeg
from guide.api import Services, create_app


@pytest.fixture
def client(seeded):
    store, proc = seeded
    return TestClient(create_app(store)), store, proc


def test_cors_preflight(client):
    c, _, _ = client
    r = c.options(
        "/runs",
        headers={"Origin": "https://x.vercel.app", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "https://x.vercel.app")


def test_home_lists_published(client):
    c, _, proc = client
    d = c.get("/display").json()
    assert d["mode"] == "home"
    assert [p["id"] for p in d["procedures"]] == [proc.id]


def test_media_served_and_traversal_blocked(client):
    c, _, proc = client
    r = c.get(f"/media/{proc.steps[0].reference_image}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.get("/media/../../etc/passwd").status_code in (400, 404)


def test_seed_images_are_display_sized(client):
    import cv2
    import numpy as np

    c, _, proc = client
    data = c.get(f"/media/{proc.steps[0].reference_image}").content
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) <= 600


def test_run_lifecycle(client):
    c, store, proc = client
    c.put("/operator", json={"operator": "Sam"})
    run = c.post("/runs", json={"procedure_id": proc.id}).json()
    assert run["operator"] == "Sam" and run["current_step"] == 0
    assert run["step"]["id"] == proc.steps[0].id
    d = c.get("/display").json()
    assert d["mode"] == "run" and d["run"]["id"] == run["id"]

    rid = run["id"]
    r = c.post(f"/runs/{rid}/next").json()
    assert r["current_step"] == 1
    r = c.post(f"/runs/{rid}/back").json()
    assert r["current_step"] == 0
    r = c.post(f"/runs/{rid}/override", json={"note": "verified by hand"}).json()
    assert r["current_step"] == 1 and r["steps"][0]["override"]["note"] == "verified by hand"
    for _ in range(len(proc.steps) - 1):
        r = c.post(f"/runs/{rid}/next").json()
    assert r["screen"] == "done" and r["ended_at"]
    d = c.get("/display").json()  # Done stays up until the wearer taps Home
    assert d["mode"] == "run" and d["run"]["screen"] == "done" and "step" not in d["run"]
    c.post("/home")
    assert c.get("/display").json()["mode"] == "home"


def test_check_without_verifier_is_503_not_pass(client):
    c, _, proc = client
    rid = c.post("/runs", json={"procedure_id": proc.id}).json()["id"]
    assert c.post(f"/runs/{rid}/check").status_code == 503


def test_frames_ring_buffer(client):
    c, store, proc = client
    rid = c.post("/runs", json={"procedure_id": proc.id}).json()["id"]
    for _ in range(9):
        assert c.post(f"/runs/{rid}/frames", content=jpeg(), headers={"Content-Type": "image/jpeg"}).status_code == 200
    assert len(store.recent_frames(rid)) == 6
    assert c.post(f"/runs/{rid}/frames", content=b"nope").status_code == 400


def test_put_procedure_validates_and_reorders(client):
    c, _, proc = client
    body = c.get(f"/procedures/{proc.id}").json()
    body["steps"] = list(reversed(body["steps"]))[:-1]
    out = c.put(f"/procedures/{proc.id}", json=body).json()
    assert out["steps"][0]["id"] == proc.steps[-1].id and len(out["steps"]) == len(proc.steps) - 1
    body["steps"][0]["title"] = "this title is far too long now"
    assert c.put(f"/procedures/{proc.id}", json=body).status_code == 422


def test_followup_answers_become_tips(client):
    c, store, proc = client
    from guide.schemas import FollowUp

    p = store.get_procedure(proc.id)
    fq = FollowUp(question="Why tug every wire?", step_id=p.steps[-1].id)
    p.followups = [fq]
    store.save_procedure(p)
    out = c.post(f"/procedures/{proc.id}/answers", json={"answers": {fq.id: "Loose wires fail later."}}).json()
    assert "Loose wires fail later." in out["steps"][-1]["tips"]
    out = c.post(f"/procedures/{proc.id}/answers", json={"answers": {fq.id: "Edited answer."}}).json()
    assert "Edited answer." in out["steps"][-1]["tips"]
    assert "Loose wires fail later." not in out["steps"][-1]["tips"]


def test_capture_recording_display(client):
    c, _, _ = client
    cap = c.post("/captures/start", json={"title": "Demo"}).json()
    d = c.get("/display").json()
    assert d["mode"] == "recording" and d["capture"]["id"] == cap["id"]
    assert c.post(f"/captures/{cap['id']}/bundle", content=b"notazip").status_code == 400
    r = c.post(f"/captures/{cap['id']}/bundle", content=b"PK\x03\x04rest").json()
    assert r["state"] == "uploaded"
    assert c.get("/display").json()["mode"] == "home"
    assert c.post(f"/captures/{cap['id']}/process").status_code == 503  # no pipeline wired


def test_process_spawns_pipeline(seeded):
    store, _ = seeded
    spawned = []
    c = TestClient(create_app(store, Services(spawn_pipeline=spawned.append)))
    cap = c.post("/captures/start").json()
    c.post(f"/captures/{cap['id']}/bundle", content=b"PK\x03\x04")
    assert c.post(f"/captures/{cap['id']}/process").json()["state"] == "processing"
    assert spawned == [cap["id"]]


def test_abandoned_capture_releases_glasses(client):
    c, store, _ = client
    cap = c.post("/captures/start").json()
    stored = store.get_capture(cap["id"])
    stored.started_at -= 31 * 60  # phone walked away 31 minutes ago
    store.save_capture(stored)
    assert c.get("/display").json()["mode"] == "home"
    assert c.get(f"/captures/{cap['id']}").json()["state"] == "error"
    # a late upload still goes through
    assert c.post(f"/captures/{cap['id']}/bundle", content=b"PK\x03\x04").json()["state"] == "uploaded"


def test_stop_without_upload_marks_abandoned(client):
    c, _, _ = client
    cap = c.post("/captures/start").json()
    out = c.post(f"/captures/{cap['id']}/stop").json()
    assert out["state"] == "error" and "before upload" in out["error"]
    assert c.get("/display").json()["mode"] == "home"


def test_diag_reports_roundtrip(client):
    c, _, _ = client
    for i in range(22):
        assert c.post("/diag", json={"run": i, "results": []}).json()["ok"]
    got = c.get("/diag").json()
    assert len(got) == 20 and got[-1]["run"] == 21 and "received_at" in got[-1]
