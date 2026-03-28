# LOCKED: This device is Waveshare 2.13 V4 ONLY.
# Do NOT change driver unless hardware changes.

from PIL import Image
from waveshare_epd import epd2in13_V4


class EInkDisplay:
    def __init__(self):
        self.epd = None
        self.width = None
        self.height = None

    def initialize(self):
        self.epd = epd2in13_V4.EPD()
        self.epd.init()
        self.epd.Clear()
        self.width = self.epd.height
        self.height = self.epd.width

    @property
    def size(self):
        return self.width, self.height

    def _normalize(self, image):
        if image.mode != "1":
            image = image.convert("1")
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        return image

    def render(self, image):
        frame = self._normalize(image)
        self.epd.display(self.epd.getbuffer(frame))

    def blank(self):
        image = Image.new("1", self.size, 255)
        self.render(image)

    def sleep(self):
        if self.epd is not None and hasattr(self.epd, "sleep"):
            self.epd.sleep()
