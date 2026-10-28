"""Small order query service."""


def list_orders(orders, customer=None, status=None):
    """Return copies of matching orders sorted by integer id."""
    allowed_statuses = ("pending", "paid", "cancelled")

    if status is not None and status not in allowed_statuses:
        raise ValueError("status must be pending, paid, or cancelled")

    return sorted(
        [
            dict(o)
            for o in orders
            if (customer is None or o["customer"] == customer)
            and (status is None or o["status"] == status)
        ],
        key=lambda o: int(o["id"]),
    )
