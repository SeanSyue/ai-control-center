def greet(name: str) -> str:
    if not name.strip():
        return "Hello, Guest"
    return f"Hello, {name}"
