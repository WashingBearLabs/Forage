"""Raw and decoded byte bounds for one HTTP response, without library decoding."""

import zlib

import httpx

_decompressobj = zlib.decompressobj


class BodyTooLargeError(Exception):
    """The response exceeded its raw or decoded byte budget."""

    def __init__(self) -> None:
        super().__init__("body_too_large")


class UnsupportedEncodingError(Exception):
    """This build cannot decode the response's content encoding."""

    def __init__(self) -> None:
        super().__init__("unsupported_encoding")


class MalformedBodyError(Exception):
    """The response is not exactly one complete compressed stream."""

    def __init__(self) -> None:
        super().__init__("malformed_body")


# Public seam names; class names retain the repository's required Error suffix.
BodyTooLarge = BodyTooLargeError
UnsupportedEncoding = UnsupportedEncodingError
MalformedBody = MalformedBodyError


async def read_bounded_body(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read identity, gzip or deflate bytes with bounded decompressor output."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip", "deflate"}:
        raise UnsupportedEncoding()

    compressed = encoding != "identity"
    raw_limit = 4 * max_bytes
    announced_limit = raw_limit if compressed else max_bytes
    content_length = response.headers.get("content-length")
    if content_length is not None and (
        len(content_length) > 20
        or (content_length.isdecimal() and int(content_length) > announced_limit)
    ):
        raise BodyTooLarge()

    decoder = (
        _decompressobj(zlib.MAX_WBITS | 16 if encoding == "gzip" else zlib.MAX_WBITS)
        if compressed
        else None
    )
    first = True
    prefix = b""
    raw_total = 0
    body = bytearray()
    async for chunk in response.aiter_raw():
        raw_total += len(chunk)
        if raw_total > raw_limit:
            raise BodyTooLarge()
        if decoder is None:
            if len(body) + len(chunk) > max_bytes:
                raise BodyTooLarge()
            body.extend(chunk)
            continue
        if decoder.eof:
            raise MalformedBody()

        data = chunk
        if first and encoding == "deflate":
            # zlib needs both header bytes to distinguish wrapped from raw
            # deflate; a transport boundary after one byte must not disable retry.
            data = prefix + data
            if len(data) < 2:
                prefix = data
                continue
            prefix = b""
        while data:
            try:
                # Zero means unlimited to zlib: the extra byte keeps an exact-cap
                # body bounded and lets us detect its first overflow byte.
                output = decoder.decompress(data, max_length=max_bytes - len(body) + 1)
            except zlib.error:
                if first and encoding == "deflate":
                    decoder = _decompressobj(-zlib.MAX_WBITS)
                    first = False
                    continue
                raise MalformedBody() from None
            first = False
            body.extend(output)
            if len(body) > max_bytes:
                raise BodyTooLarge()
            if decoder.unused_data:
                raise MalformedBody()
            tail = decoder.unconsumed_tail
            if not output and len(tail) == len(data):
                raise MalformedBody()
            data = tail

        # An incomplete stream has spent its entire raw budget. Do not wait for
        # another chunk from a zero-output trickler just to discover an overrun.
        if raw_total == raw_limit and not decoder.eof:
            raise BodyTooLarge()

    if decoder is not None and (not decoder.eof or decoder.unconsumed_tail):
        raise MalformedBody()
    return bytes(body)
