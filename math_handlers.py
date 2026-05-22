from typing import Callable

"""
All methods exported in MATH_HANDLERS must accept one int argument representing
the number to be expanded, and return a str representing the math message to send.
"""

def expand_sixseven( x: int ) -> str:
        """
        Expand the given value into "67 + 67 + ... + m" where m is the remainder of x / 67.
        """
        parts = []

        while x >= 67:
            parts.append( "67" )
            x -= 67

        if x > 0:
            parts.append( str( x ) )

        return "+".join( parts )

MATH_HANDLERS: dict[str, Callable[[int], str]] = {
    "sixseven": expand_sixseven
}