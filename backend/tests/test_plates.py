import re

import cv2
import numpy as np
import pytest

from watchful.components import make_ocr, make_plate_reader
from watchful.config import Settings
from watchful.tuning import Tuning
from watchful.vision.ocr import ctc_greedy
from watchful.vision.plates import (CropRead, LpdYunet, crop_confidence, load_grammar, parse, plate_colour,
                                    read_plate, split_lines, vote)

WHITE, YELLOW, BLACK = (250, 250, 250), (30, 205, 245), (20, 20, 20)
GREEN, BLUE = (60, 150, 30), (150, 70, 20)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def settings(**kw):
    return Settings(session_secret="y", _env_file=None, **kw)


@pytest.fixture(scope="module")
def reader():
    return make_plate_reader(settings(), Tuning())


def plate(lines: list[str], bg=WHITE, fg=BLACK, size=(400, 96), ind=True) -> np.ndarray:
    """An Indian plate as OpenCV draws it: an embossed border, the blue IND strip, lines centred."""
    w, h = size
    img = np.full((h, w, 3), bg, np.uint8)
    cv2.rectangle(img, (4, 4), (w - 5, h - 5), fg, 2, cv2.LINE_AA)
    left = 8
    if ind:
        strip = max(18, w // 14)
        cv2.rectangle(img, (8, 8), (8 + strip, h - 9), BLUE, -1)
        cv2.putText(img, "IND", (10, h - 14), FONT, strip / 70, WHITE, 1, cv2.LINE_AA)
        left = 8 + strip + 4
    line_h = (h - 16) / len(lines)
    for i, text in enumerate(lines):
        (tw, th), _ = cv2.getTextSize(text, FONT, 1.0, 2)
        scale = min((w - left - 12) / tw, line_h * 0.62 / th)
        thick = max(2, round(scale * 2.4))
        (tw, th), _ = cv2.getTextSize(text, FONT, scale, thick)
        x = left + (w - left - 8 - tw) // 2
        y = int(8 + line_h * i + (line_h + th) / 2)
        cv2.putText(img, text, (x, y), FONT, scale, fg, thick, cv2.LINE_AA)
    return img


def vehicle(plate_img: np.ndarray, body=(40, 40, 150), tilt=0.12, seed=0, size=(480, 360),
            plate_width=0.34, blur=1.0, wall=(170, 160, 150), road=(95, 100, 105)) -> np.ndarray:
    """The back of a car seen by a gate camera a little from one side, with sensor noise and JPEG."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.empty((h, w, 3), np.uint8)
    img[: h // 3] = wall
    img[h // 3:] = road
    cv2.rectangle(img, (int(w * 0.08), int(h * 0.12)), (int(w * 0.92), int(h * 0.9)), body, -1, cv2.LINE_AA)
    cv2.rectangle(img, (int(w * 0.18), int(h * 0.16)), (int(w * 0.82), int(h * 0.4)), (45, 40, 35), -1)  # glass
    for x in (0.1, 0.78):
        cv2.rectangle(img, (int(w * x), int(h * 0.45)), (int(w * (x + 0.12)), int(h * 0.56)), (30, 30, 200), -1)
    cv2.rectangle(img, (int(w * 0.08), int(h * 0.8)), (int(w * 0.92), int(h * 0.9)), (35, 35, 35), -1)  # bumper
    pw = int(w * plate_width)
    ph = round(pw * plate_img.shape[0] / plate_img.shape[1])
    x0, y0 = (w - pw) // 2, int(h * 0.76) - ph
    img[y0:y0 + ph, x0:x0 + pw] = cv2.resize(plate_img, (pw, ph), interpolation=cv2.INTER_AREA)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[0, h * tilt / 2], [w, 0], [w, h], [0, h * (1 - tilt / 2)]])
    img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h), borderMode=cv2.BORDER_REPLICATE)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    img = np.clip(img + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1], cv2.IMREAD_COLOR)


def far(image: np.ndarray, factor: float) -> np.ndarray:
    """The same view from a camera this many times further away, scaled back up."""
    h, w = image.shape[:2]
    small = cv2.resize(image, (round(w * factor), round(h * factor)), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h))


def ir(image: np.ndarray) -> np.ndarray:
    """A night frame from an IR camera: one channel, the retroreflective plate bright."""
    g = cv2.convertScaleAbs(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), alpha=0.8, beta=10)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


# reading rendered plates end to end

def test_white_plate(reader):
    got = read_plate([vehicle(plate(["MH 12 AB 1234"]))], reader)
    assert got.text == "MH12AB1234" and got.colour == "white" and got.confidence > 0.5


def test_yellow_plate_is_commercial(reader):
    got = read_plate([vehicle(plate(["KA 01 AB 2345"], bg=YELLOW), body=(200, 200, 200), tilt=-0.1, seed=1)],
                     reader)
    assert got.text == "KA01AB2345" and got.colour == "yellow"


def test_two_line_plate(reader):
    two = plate(["UP 32", "GH 5678"], size=(272, 160), ind=False)
    assert len(split_lines(cv2.resize(two, (154, 96)))) == 2
    got = read_plate([vehicle(two, body=(130, 130, 40), plate_width=0.26, seed=2)], reader)
    assert got.text == "UP32GH5678" and got.colour == "white"


def test_grey_ir_frame_gives_colour_unknown(reader):
    got = read_plate([ir(vehicle(plate(["KA 01 AB 2345"], bg=YELLOW), seed=3))], reader)
    assert got.colour == "unknown"
    assert got.text == "KA01AB2345"  # IR plates still read


def test_voting_fixes_one_bad_crop(reader):
    good = [vehicle(plate(["TN 09 BC 4821"]), seed=s) for s in (4, 5)]
    bad = far(good[0], 0.25)  # the car still far from the gate: a quarter of the pixels
    misread = reader.read_crop(bad)
    assert misread.text not in (None, "TN09BC4821")  # a wrong read that still fits the grammar
    got = read_plate([good[0], bad, good[1]], reader)
    assert got.text == "TN09BC4821" and misread.text in got.reads
    assert got.confidence < read_plate(good, reader).confidence


def test_text_boxes_when_no_plate_box():
    reader = make_plate_reader(settings(plate_detector="none"), Tuning())
    got = read_plate([vehicle(plate(["MH 12 AB 1234"]), seed=7)], reader)
    assert got.text == "MH12AB1234" and got.colour == "white"


def test_lpd_yunet_finds_the_plate(reader):
    car = vehicle(plate(["MH 12 AB 1234"]), plate_width=0.45)  # scores 0.79 to 0.98 over seeds and tilts
    found = LpdYunet(settings=settings()).find(car)
    assert found and found[0][1] >= Tuning().plates.detect_score + 0.15
    cx, cy = found[0][0].mean(0)
    assert 132 < cx < 348 and 222 < cy < 274  # inside the plate pasted above the bumper
    got = reader.read_crop(car)
    assert got.text == "MH12AB1234" and got.via in ("plate box", "text rows around the plate box")


def test_black_cab_in_daylight_is_yellow(reader):
    cab = vehicle(plate(["MH 01 AB 4521"], bg=YELLOW), body=(22, 22, 22), wall=(80, 80, 80), road=(60, 62, 65),
                  seed=8)
    assert cv2.cvtColor(cab, cv2.COLOR_BGR2HSV)[..., 2].mean() < Tuning().plates.dark_v  # a dark crop overall
    got = read_plate([cab], reader)
    assert got.text == "MH01AB4521" and got.colour == "yellow"


# grammar, voting and colour on their own

@pytest.mark.parametrize("raw,text,fixes", [
    ("MH12AB1234", "MH12AB1234", 0),
    ("MH 12 A8 1234", "MH12AB1234", 1),
    ("MHI2ABI234", "MH12AB1234", 2),
    ("0L3CAB1234", "DL3CAB1234", 1),
    ("IND MH 12 AB 1234", "MH12AB1234", 0),
    ("KA01AB2345.", "KA01AB2345", 0),
    ("22 BH 1234 AA", "22BH1234AA", 0),
    ("22 8H 1234 AA", "22BH1234AA", 1),
    ("MB 08 6787", "WB086787", 1),  # W read as M: state codes try letters read for one another
    ("INDDL2SN6252", "DL2SN6252", 0),
    ("MH12AB12341", "MH12AB1234", 0),  # a stray character after the plate is left out
    ("MH12AB1234 IND", "MH12AB1234", 0),
    (["KA19", "INDEQ1316"], "KA19EQ1316", 0),  # the IND strip at the start of a plate's second line
])
def test_grammar_fixes_by_position(raw, text, fixes):
    got = parse(raw)
    assert got is not None and got.text == text and got.fixes == fixes


@pytest.mark.parametrize("raw", ["HELLO", "XX12AB1234", "MH12AB", "", "MH12KK12KK", "KL6D827"])
def test_grammar_refuses_what_is_not_a_plate(raw):
    assert parse(raw) is None


def test_ind_is_only_taken_off_the_ends():
    got = parse("KA0IND1234")
    assert got is None or got.text != "KA01234"


def test_confidence_counts_cost_crops_and_agreement():
    cfg = Tuning().plates
    exact, bent = parse("MH12AB1234"), parse("MH12A81234")
    assert bent.text == exact.text and crop_confidence(bent, 0.95, 0.9) < crop_confidence(exact, 0.95, 0.9)
    assert crop_confidence(exact, 0.95, cfg.rows_score) < crop_confidence(exact, 0.95, 0.9)  # no plate box
    one = CropRead("MH12AB1234", crop_confidence(exact, 0.95, 0.9), "white")
    alone, agreed = vote([one]), vote([one, one])
    assert alone.text == agreed.text == "MH12AB1234" and alone.confidence < agreed.confidence
    with_unread = vote([one, CropRead(None, 0.0, "unknown"), CropRead(None, 0.0, "unknown")])
    assert with_unread.confidence < alone.confidence
    # the last character cut off still fits the grammar as another plate: alone it is not trusted
    cut = parse("MH12AB123")
    assert cut.text != "MH12AB1234"
    guess = vote([CropRead(cut.text, crop_confidence(cut, 0.95, 0.9), "white")])
    assert guess.text is None and guess.confidence < cfg.min_conf and guess.reads == (cut.text,)
    lone = vote([CropRead("KA1SE0131", 0.86, "white"), CropRead(None, 0.0, "unknown"), CropRead(None, 0.0, "unknown")])
    assert lone.text is None and lone.confidence < cfg.min_conf


def test_every_format_example_reads_as_itself():
    import json
    from watchful.vision.plates import DATA
    data = json.loads((DATA / "plates.json").read_text())
    assert len(load_grammar().states) == len(data["states"]) >= 36
    for f in data["formats"]:
        assert re.match(f["pattern"], f["example"])
        assert parse(f["example"]).text == f["example"]


def test_vote_char_by_char():
    reads = [CropRead("MH12AB1284", 0.9, "white"), CropRead("MH12AE1234", 0.9, "white"),
             CropRead("MH12AB1234", 0.8, "unknown")]
    got = vote(reads)
    assert got.text == "MH12AB1234" and got.colour == "white" and 0 < got.confidence < 0.9
    tie = vote([CropRead("MH12AB1234", 0.9, "white"), CropRead("MH12AB1284", 0.9, "white")])
    assert tie.text is None and tie.reads == ("MH12AB1234", "MH12AB1284")
    unread = vote([CropRead(None, 0.0, "yellow"), CropRead(None, 0.0, "unknown")])
    assert unread.text is None and unread.confidence == 0 and unread.colour == "yellow"


def test_green_and_black_plates():
    frame = vehicle(plate(["MH 12 AB 1234"]))
    assert plate_colour(plate(["MH 12 EV 1234"], bg=GREEN, fg=WHITE), frame) == "green"
    assert plate_colour(plate(["MH 12 SD 1234"], bg=BLACK, fg=YELLOW), frame) == "black"
    assert plate_colour(plate(["MH 12 AB 1234"]), ir(frame)) == "unknown"
    night = (plate(["MH 12 AB 1234"]) * 0.2).astype(np.uint8)  # the plate itself too dark to judge
    assert plate_colour(night, (frame * 0.2).astype(np.uint8)) == "unknown"


def test_crnn_stays_selectable():
    crnn = make_ocr(settings(ocr="ppocr_v3_crnn"), Tuning())
    assert crnn.read_line(plate(["MH 12"], size=(200, 96), ind=False)[12:-12, 12:-12])[0] == "mh12"


def test_ctc_greedy_decode():
    steps = [1, 1, 0, 1, 2, 2, 0, 3, 0]  # a a _ a b b _ c _
    probs = np.full((len(steps), 4), 0.1 / 3, np.float32)
    probs[np.arange(len(steps)), steps] = 0.9
    text, score = ctc_greedy(probs, list("abc"))
    assert text == "aabc"
    assert score == pytest.approx(0.9)
