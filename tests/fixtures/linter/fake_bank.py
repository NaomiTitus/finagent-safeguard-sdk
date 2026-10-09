"""Stand-in for the bank's client. Importing this makes a module risky."""


class BankClient:
    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        raise NotImplementedError
