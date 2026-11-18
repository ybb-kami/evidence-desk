"""Match explicit scientific units attached to numeric claims across English and Chinese."""
from decimal import Decimal
import re
import unicodedata

_UNITS = {
    "ns": ("ns","nanosecond","nanoseconds","纳秒"),
    "us": ("μs","µs","microsecond","microseconds","微秒"),
    "ms": ("ms","millisecond","milliseconds","毫秒"),
    "s": ("s","sec","second","seconds","秒"),
    "min": ("min","minute","minutes","分钟"),
    "h": ("h","hour","hours","小时"),
    "Hz": ("Hz","hertz","赫兹"), "kHz": ("kHz","千赫兹"),
    "MHz": ("MHz","兆赫兹"), "GHz": ("GHz","吉赫兹"),
    "MW": ("MW","megawatt","megawatts","兆瓦"), "kW": ("kW","kilowatt","kilowatts","千瓦"),
    "µW": ("µW","μW","microwatt","microwatts","微瓦"),
    "mW": ("mW","milliwatt","milliwatts","毫瓦"), "W": ("W","watt","watts","瓦"),
    "mV": ("mV","millivolt","millivolts","毫伏"), "V": ("V","volt","volts","伏"),
    "mg": ("mg","milligram","milligrams","毫克"), "g": ("g","gram","grams","克"),
    "kg": ("kg","kilogram","kilograms","千克","公斤"),
    "mm": ("mm","millimeter","millimeters","毫米"), "cm": ("cm","centimeter","centimeters","厘米"),
    "m": ("m","meter","meters","米"), "km": ("km","kilometer","kilometers","千米","公里"),
    "B": ("byte","bytes","字节"), "MB": ("MB","megabyte","megabytes"), "GB": ("GB","gigabyte","gigabytes"),
    "MiB": ("MiB","mebibyte","mebibytes"), "GiB": ("GiB","gibibyte","gibibytes"),
    "Pa": ("Pa","pascal","pascals","帕斯卡"), "kPa": ("kPa","千帕"), "MPa": ("MPa","兆帕"),
    "°C": ("°C","摄氏度"), "°F": ("°F","华氏度"),
}
_UNITS_BY_NAME = {unicodedata.normalize("NFKC",alias):key for key,aliases in _UNITS.items() for alias in aliases}
_ALIASES = "|".join(re.escape(k) for k in sorted(_UNITS_BY_NAME,key=len,reverse=True))
_PATTERN = re.compile(r"(?<![\w.])([+−-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*("+_ALIASES+r")(?![a-zA-Z])")


def number_key(value):
    spelling = value.replace(",","").replace("−","-")
    try:
        number = Decimal(spelling)
    except ArithmeticError:
        return "literal",spelling
    sign, digits, exponent = number.as_tuple()
    if not any(digits):
        return 0,(0,),0
    while len(digits)>1 and digits[-1]==0:
        digits=digits[:-1]
        exponent+=1
    return sign,digits,exponent


def supports_units(claim, quote):
    def readings(text):
        text=unicodedata.normalize("NFKC",text)
        # CJK sentences do not use whitespace before a number.
        text=re.sub(r"(?<=[\u3400-\u9fff])(?=[+−-]?\d)", " ",text)
        return {(number_key(m[1]),_UNITS_BY_NAME[m[2]]) for m in _PATTERN.finditer(text)}
    return readings(claim) <= readings(quote)
