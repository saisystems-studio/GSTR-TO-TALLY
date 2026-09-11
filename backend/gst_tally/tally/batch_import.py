from .voucher_builder import build_voucher_batch


def iter_voucher_batches(vouchers, company, period=None, batch_size=500, max_xml_bytes=8 * 1024 * 1024):
    """Yield bounded voucher batches and their single multi-message envelope."""
    current = []
    for voucher in vouchers:
        candidate = current + [voucher]
        payload = build_voucher_batch(candidate, company, period)
        if current and (len(candidate) > batch_size or len(payload) > max_xml_bytes):
            payload = build_voucher_batch(current, company, period)
            yield current, payload
            current = [voucher]
        else:
            current = candidate
    if current:
        yield current, build_voucher_batch(current, company, period)


def batch_response_is_complete(response, expected_count):
    """Only a clean, exact Tally acknowledgement can mark a whole batch imported."""
    return bool(response and response.errors == 0 and response.exceptions == 0
                and response.cancelled == 0 and response.created + response.altered == expected_count)
