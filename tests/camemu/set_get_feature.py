#!/usr/bin/env python3
"""Verify the pylonsrc set-feature and get-feature action signals.

Writes and reads back camera features through the signals, including a
selected feature, and checks each failure result: missing feature, selector or
selector value, read-only feature, invalid and out of range values, a feature
without a value, and no open camera. Also checks that an element that opened
its camera but was never started releases it. If the camera has a lookup table
register (LUTValueAll), it also checks that a register write must fill it.
"""

import argparse
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

OK, NOT_FOUND, NOT_WRITABLE, INVALID_VALUE, FAILED = range(5)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    return parser.parse_args()


def set_feature(src, name, value, selector=None, selector_value=None):
    return src.emit("set-feature", name, value, selector, selector_value)


def get_feature(src, name, selector=None, selector_value=None):
    return src.emit("get-feature", name, selector, selector_value)


def main():
    args = parse_args()
    Gst.init(None)
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    def expect(result, want, what):
        check(
            result == want, "{}: result {}, want {}".format(what, result, want)
        )

    # Without an open camera both signals must fail, not crash.
    idle = Gst.ElementFactory.make("pylonsrc", None)
    expect(
        set_feature(idle, "Gain", "0"), FAILED, "set-feature without a camera"
    )
    check(
        get_feature(idle, "Gain") is None,
        "get-feature without a camera returned a value",
    )

    # An element dropped after opening its camera through a child, without
    # ever starting, must release the camera for the next element.
    for attempt in range(3):
        element = Gst.ElementFactory.make("pylonsrc", None)
        element.set_property("device-serial-number", args.serial)
        cam = element.get_child_by_name("cam")
        check(
            cam is not None,
            "camera did not open for element {}".format(attempt),
        )
        del cam, element

    pipeline = Gst.parse_launch(
        "pylonsrc name=src device-serial-number={serial} "
        "! video/x-raw,format=GRAY8,width=640,height=480 "
        "! fakesink".format(serial=args.serial)
    )
    src = pipeline.get_by_name("src")
    pipeline.set_state(Gst.State.PAUSED)
    pipeline.get_state(10 * Gst.SECOND)
    try:
        # A plain feature, written in its string form and read back.
        gain = get_feature(src, "Gain")
        check(gain is not None, "get-feature Gain returned nothing")
        if gain is not None:
            expect(
                set_feature(src, "Gain", gain),
                OK,
                "set-feature Gain to its value",
            )
            check(
                get_feature(src, "Gain") == gain,
                "Gain changed after writing its own value",
            )
        expect(set_feature(src, "Gain", "abc"), INVALID_VALUE, "Gain=abc")
        expect(set_feature(src, "Gain", "1e12"), INVALID_VALUE, "Gain=1e12")

        # A selected feature: written and read for one selector value, and
        # the selector is restored afterwards.
        selector_before = get_feature(src, "TriggerSelector")
        for mode in ("On", "Off"):
            expect(
                set_feature(
                    src, "TriggerMode", mode, "TriggerSelector", "FrameStart"
                ),
                OK,
                "TriggerMode-FrameStart={}".format(mode),
            )
            check(
                get_feature(
                    src, "TriggerMode", "TriggerSelector", "FrameStart"
                )
                == mode,
                "TriggerMode-FrameStart does not read back {}".format(mode),
            )
        check(
            get_feature(src, "TriggerSelector") == selector_before,
            "TriggerSelector not restored to {}".format(selector_before),
        )

        # Missing features, selectors and selector values.
        expect(
            set_feature(src, "NoSuchFeature", "1"),
            NOT_FOUND,
            "unknown feature",
        )
        check(
            get_feature(src, "NoSuchFeature") is None,
            "get-feature of an unknown feature returned a value",
        )
        expect(
            set_feature(src, "TriggerMode", "On", "NoSuchSelector", "X"),
            NOT_FOUND,
            "unknown selector",
        )
        expect(
            set_feature(
                src, "TriggerMode", "On", "TriggerSelector", "NoSuchTrigger"
            ),
            NOT_FOUND,
            "unknown selector value",
        )
        expect(
            set_feature(src, "TriggerMode", "On", "TriggerSelector", None),
            FAILED,
            "selector without a value",
        )

        # A read-only feature, and a feature without a value.
        check(
            bool(get_feature(src, "DeviceModelName")),
            "get-feature DeviceModelName returned nothing",
        )
        expect(
            set_feature(src, "DeviceModelName", "x"),
            NOT_WRITABLE,
            "read-only DeviceModelName",
        )
        expect(
            set_feature(src, "TriggerSoftware", "1"),
            FAILED,
            "command TriggerSoftware",
        )

        # A register must be written whole.
        lut = get_feature(src, "LUTValueAll", "LUTSelector", "Luminance")
        if lut is None:
            print("no LUTValueAll on this camera: register checks skipped")
        else:
            expect(
                set_feature(
                    src, "LUTValueAll", lut, "LUTSelector", "Luminance"
                ),
                OK,
                "LUTValueAll to its value",
            )
            expect(
                set_feature(
                    src, "LUTValueAll", lut[:102], "LUTSelector", "Luminance"
                ),
                INVALID_VALUE,
                "LUTValueAll with 50 bytes",
            )
            check(
                get_feature(src, "LUTValueAll", "LUTSelector", "Luminance")
                == lut,
                "LUTValueAll changed after a rejected short write",
            )
    finally:
        pipeline.set_state(Gst.State.NULL)

    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
