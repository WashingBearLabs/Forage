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


class _BoundedDecoder:
    def __init__(self, wbits: int, max_bytes: int) -> None:
        self.decoder = _decompressobj(wbits)
        self.max_bytes = max_bytes
        self.body = bytearray()

    def feed(self, data: bytes | bytearray) -> None:
        if self.decoder.eof:
            raise MalformedBody()
        while data:
            try:
                # Zero means unlimited to zlib: the extra byte keeps an exact-cap
                # body bounded and lets us detect its first overflow byte.
                output = self.decoder.decompress(
                    data, max_length=self.max_bytes - len(self.body) + 1
                )
            except zlib.error:
                raise MalformedBody() from None
            self.body.extend(output)
            if len(self.body) > self.max_bytes:
                raise BodyTooLarge()
            if self.decoder.unused_data:
                raise MalformedBody()
            tail = self.decoder.unconsumed_tail
            if not output and len(tail) == len(data):
                raise MalformedBody()
            data = tail

    def finish(self) -> None:
        if not self.decoder.eof or self.decoder.unconsumed_tail:
            raise MalformedBody()


async def read_bounded_body(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Bound decoded output and raw input, including deflate's replay history."""
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
        _BoundedDecoder(
            zlib.MAX_WBITS | 16 if encoding == "gzip" else zlib.MAX_WBITS, max_bytes
        )
        if compressed
        else None
    )
    # Raw deflate can share a valid zlib header and fail the wrapped interpretation
    # only after emitting output. Keep a raw-budget-bounded replay until it is
    # validated, rather than letting transport chunk boundaries choose the format.
    replay = bytearray() if encoding == "deflate" else None
    wrapped_overflow = False
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
        if replay is not None:
            replay.extend(chunk)
        try:
            decoder.feed(chunk)
        except (MalformedBody, BodyTooLarge) as error:
            if replay is None:
                if wrapped_overflow:
                    raise BodyTooLarge() from None
                raise
            # Even overflow can belong to the wrong interpretation. Retry once,
            # under the same output cap, without keeping its speculative output.
            wrapped_overflow = isinstance(error, BodyTooLarge)
            decoder.body.clear()
            decoder = _BoundedDecoder(-zlib.MAX_WBITS, max_bytes)
            try:
                decoder.feed(replay)
            except MalformedBody:
                if wrapped_overflow:
                    raise BodyTooLarge() from None
                raise
            replay = None

        # An incomplete stream has spent its entire raw budget. Do not wait for
        # another chunk from a zero-output trickler just to discover an overrun.
        if raw_total == raw_limit and not decoder.decoder.eof:
            if replay is not None:
                decoder.body.clear()
                decoder = _BoundedDecoder(-zlib.MAX_WBITS, max_bytes)
                try:
                    decoder.feed(replay)
                except MalformedBody:
                    raise BodyTooLarge() from None
                replay = None
            if not decoder.decoder.eof:
                raise BodyTooLarge()

    if decoder is not None:
        try:
            decoder.finish()
        except MalformedBody:
            if replay is None:
                if wrapped_overflow:
                    raise BodyTooLarge() from None
                raise
            decoder.body.clear()
            decoder = _BoundedDecoder(-zlib.MAX_WBITS, max_bytes)
            decoder.feed(replay)
            decoder.finish()
        return bytes(decoder.body)
    return bytes(body)
