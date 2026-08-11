export async function request(path, options = {}) {
    const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    if (!response.ok) {
        throw new Error(`${response.status}: ${await response.text()}`);
    }
    if (response.status === 204)
        return null;
    const text = await response.text();
    return text ? JSON.parse(text) : null;
}
