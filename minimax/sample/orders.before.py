"""Small order query service. Original example authored for this submission."""
def list_orders(orders, customer=None, status=None):
    """Return orders for a customer, sorted by integer id; never mutate input."""
    return sorted([dict(o) for o in orders
                   if customer is None or o["customer"] == customer],
                  key=lambda o: o["id"])