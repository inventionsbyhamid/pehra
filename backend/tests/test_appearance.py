import numpy as np

from watchful.vision.appearance import SAME_THRESHOLD, TrackSighting, link_tracks, same_person, signature

BOX = (20.0, 10.0, 60.0, 160.0)


def person(shirt_bgr, light=1.0, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((200, 100, 3), (90, 90, 90), np.float32)
    img[40:110, 20:80] = shirt_bgr
    img = img * light + rng.normal(0, 6, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


GREEN, RED = (40, 160, 40), (40, 40, 190)


def test_same_shirt_close():
    assert same_person(signature(person(GREEN), BOX), signature(person(GREEN, 0.75, 1), BOX)) < SAME_THRESHOLD


def test_different_shirts_far():
    assert same_person(signature(person(GREEN), BOX), signature(person(RED), BOX)) > SAME_THRESHOLD


def sighting(tid, t0, t1, shirt, person_id=None, name=None, seed=0):
    return TrackSighting(tid, t0, t1, signature(person(shirt, seed=seed), BOX), None, person_id, name)


def test_link_tracks_groups_reappearance():
    groups = link_tracks([sighting(1, 0, 60, GREEN), sighting(2, 3600, 3700, GREEN, seed=1),
                          sighting(3, 7200, 7300, GREEN, seed=2)])
    assert len(groups) == 1 and groups[0].track_ids == [1, 2, 3]
    assert groups[0].first_ts == 0 and groups[0].last_ts == 7300


def test_two_lookalikes_flagged():
    groups = link_tracks([sighting(1, 0, 100, GREEN), sighting(2, 50, 150, GREEN, seed=1)])
    assert len(groups) == 2 and all(g.ambiguous for g in groups)


BLUE = (180, 60, 30)


def test_face_label_groups_and_carries_by_clothes():
    # face matches name tracks 1 and 2 even in different clothes; track 3 has no face but the same
    # clothes 40 s after track 1, so the name carries over; track 4 is someone else
    groups = link_tracks([sighting(1, 0, 60, GREEN, "p1", "Sana"), sighting(2, 3600, 3700, RED, "p1", "Sana"),
                          sighting(3, 100, 200, GREEN, seed=3), sighting(4, 120, 220, BLUE)])
    sana = next(g for g in groups if g.person_id == "p1")
    assert sana.track_ids == [1, 3, 2] and sana.label == "Sana"  # time order
    assert len(groups) == 2
