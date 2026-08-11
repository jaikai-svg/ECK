export class SlashCommandModel {
    commands = [];
    matches = [];
    selection = 0;
    setCommands(value) {
        this.commands = Array.isArray(value) ? value.filter(this.isCommand) : [];
        this.close();
    }
    update(value, limit = 10) {
        if (!value.startsWith("/") || value.includes("\n")) {
            return this.close();
        }
        const query = value.toLocaleLowerCase();
        this.matches = this.commands.filter((item) => {
            const command = item.command.toLocaleLowerCase();
            const insert = String(item.insert ?? "").toLocaleLowerCase();
            return command.startsWith(query) || insert.startsWith(query);
        }).slice(0, limit);
        this.selection = Math.min(this.selection, Math.max(0, this.matches.length - 1));
        return this.matches;
    }
    move(direction) {
        if (!this.matches.length)
            return this.selection;
        this.selection = (this.selection + direction + this.matches.length) % this.matches.length;
        return this.selection;
    }
    at(index = this.selection) {
        return this.matches[index];
    }
    close() {
        this.matches = [];
        this.selection = 0;
        return this.matches;
    }
    isCommand(value) {
        return Boolean(value
            && typeof value === "object"
            && "command" in value
            && typeof value.command === "string");
    }
}
