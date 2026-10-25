"""Where an Overlay sits on the footage's frame, as recorded in the sidecar for placing it."""

from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]
"""The corner of the frame an Overlay sits in."""


class PlacementError(Exception):
    """An Overlay doesn't fit its frame; the message says by how much."""


class Placement(BaseModel, frozen=True):
    """An Overlay's place on the frame, in pixels."""

    corner: Corner
    """The frame's corner it sits in."""
    margin: int
    """Gap between it and the frame's edges beside its corner."""
    x: int
    y: int
    """Its top-left pixel, from the frame's top-left."""
    width: int
    height: int


def place(corner: Corner, margin: float, width: int, height: int, frame: tuple[int, int]) -> Placement:
    """An Overlay `width` by `height` pixels in `corner` of a frame (width, height) pixels, `margin` of the frame's
    height from the edges beside it."""
    frame_width, frame_height = frame
    gap = round(margin * frame_height)
    if width + 2 * gap > frame_width or height + 2 * gap > frame_height:
        raise PlacementError(
            f"A {width}×{height} Overlay with a {gap}-pixel margin doesn't fit a {frame_width}×{frame_height} frame"
        )
    vertical, horizontal = corner.split("-")
    return Placement(
        corner=corner,
        margin=gap,
        x=gap if horizontal == "left" else frame_width - gap - width,
        y=gap if vertical == "top" else frame_height - gap - height,
        width=width,
        height=height,
    )


def in_frame(overlay: NDArray[np.uint8], placement: Placement, frame: tuple[int, int]) -> NDArray[np.uint8]:
    """The Overlay drawn in its place on a transparent frame (width, height) pixels, for editors that won't place
    it."""
    width, height = frame
    canvas = np.zeros((height, width, 4), np.uint8)
    canvas[placement.y : placement.y + placement.height, placement.x : placement.x + placement.width] = overlay
    return canvas
