import raw from "../../../src/evalkit/contracts/notes.json?raw";

const notes = JSON.parse(raw) as {
  endpoint_note: string;
  personal_key_note: string;
};

export const ENDPOINT_NOTE = notes.endpoint_note;
export const PERSONAL_KEY_NOTE = notes.personal_key_note;
