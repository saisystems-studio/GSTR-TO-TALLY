from .voucher_builder import build_voucher_batch


def iter_voucher_batches(vouchers, company, period=None, batch_size=500, max_xml_bytes=8 * 1024 * 1024):
    """Yield bounded, single-build multi-voucher envelopes.

    The previous implementation rebuilt the complete growing XML envelope for
    every candidate voucher, making a 500-row chunk quadratic before it was
    ever sent to Tally. Build each normal chunk once; only an oversized
    payload is recursively split into smaller bounded chunks.
    """
    size = max(1, int(batch_size or 1))

    def bounded(items):
        payload = build_voucher_batch(items, company, period)
        if len(payload) <= max_xml_bytes or len(items) == 1:
            yield items, payload
            return
        midpoint = len(items) // 2
        yield from bounded(items[:midpoint])
        yield from bounded(items[midpoint:])

    for start in range(0, len(vouchers), size):
        yield from bounded(vouchers[start:start + size])


def batch_response_is_complete(response, expected_count):
    """Only a clean, exact Tally acknowledgement can mark a whole batch imported."""
    return bool(response and response.errors == 0 and response.exceptions == 0
                and response.cancelled == 0 and response.created + response.altered == expected_count)
