from PIL import Image
from waveshare_epd import epd2in13


class EInkDisplay:
    def __init__(self):
        self.epd = None
        self.native_width = None
        self.native_height = None
        self.width = None
        self.height = None

    def initialize(self):
        self.epd = epd2in13.EPD()
        init_mode = getattr(self.epd, "FULL_UPDATE", None)
        if init_mode is None:
            self.epd.init()
        else:
            self.epd.init(init_mode)

        try:
            self.epd.Clear(0xFF)
        except TypeError:
            self.epd.Clear()

        self.native_width = getattr(self.epd, "width", getattr(epd2in13, "EPD_WIDTH", 122))
        self.native_height = getattr(self.epd, "height", getattr(epd2in13, "EPD_HEIGHT", 250))
        self.width = max(self.native_width, self.native_height)
        self.height = min(self.native_width, self.native_height)

    @property
    def size(self):
        return self.width, self.height

    def _normalize(self, image):
        if image.mode != "1":
            image = image.convert("1")

        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))

        if self.native_width < self.native_height:
            return image.rotate(90, expand=True)
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
