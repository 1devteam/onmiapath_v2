-- Atomically acknowledge one contiguous archive batch without double-decrementing lag.
-- KEYS: 1 meta hash, 2 outbox stream. ARGV: group, prior seq, new seq, entry IDs...

local function fail(reason) return {"REJECTED", reason} end
local function canonical_uint(value)
    return type(value) == "string"
        and (value == "0" or string.match(value, "^[1-9][0-9]*$") ~= nil)
end
local function strip_zeroes(value)
    local stripped = string.gsub(value, "^0+", "")
    return stripped == "" and "0" or stripped
end
local function compare(left, right)
    left, right = strip_zeroes(left), strip_zeroes(right)
    if string.len(left) ~= string.len(right) then
        return string.len(left) > string.len(right) and 1 or -1
    end
    if left == right then return 0 end
    return left > right and 1 or -1
end
local function subtract(left, right)
    if compare(left, right) < 0 then return nil end
    local output, borrow, j = {}, 0, string.len(right)
    for i = string.len(left), 1, -1 do
        local a = string.byte(left, i) - 48 - borrow
        local b = j > 0 and string.byte(right, j) - 48 or 0
        j = j - 1
        if a < b then a, borrow = a + 10, 1 else borrow = 0 end
        table.insert(output, 1, string.char(48 + a - b))
    end
    return strip_zeroes(table.concat(output))
end

if #KEYS ~= 2 or #ARGV < 4 then return fail("abi_arity") end
if redis.call("TYPE", KEYS[1])["ok"] ~= "hash"
    or redis.call("TYPE", KEYS[2])["ok"] ~= "stream" then
    return fail("wrong_key_type")
end
local group, prior, newest = ARGV[1], ARGV[2], ARGV[3]
local stored = redis.call("HGET", KEYS[1], "archive_ack_sequence")
local count = redis.call("HGET", KEYS[1], "unarchived_count")
if not canonical_uint(prior) or not canonical_uint(newest)
    or not canonical_uint(stored) or not canonical_uint(count)
    or compare(newest, prior) <= 0 then
    return fail("invalid_sequence")
end
if stored == newest then
    return {"REPLAYED", newest}
end
if stored ~= prior then return fail("stale_checkpoint") end
local decrement = subtract(newest, prior)
if decrement == nil or compare(count, decrement) < 0 then
    return fail("broken_archive_count")
end
local remaining = subtract(count, decrement)
local next_oldest = ""
if remaining ~= "0" then
    local entries = redis.call("XRANGE", KEYS[2], "-", "+")
    for _, entry in ipairs(entries) do
        local fields, sequence, record_json = entry[2], nil, nil
        for index = 1, #fields, 2 do
            if fields[index] == "sequence" then sequence = fields[index + 1] end
            if fields[index] == "record_json" then record_json = fields[index + 1] end
        end
        if canonical_uint(sequence) and compare(sequence, newest) > 0 then
            local ok, record = pcall(cjson.decode, record_json)
            if not ok or type(record) ~= "table" or type(record.created_at) ~= "string" then
                return fail("invalid_next_record")
            end
            -- XADD uses the Redis server epoch in the generated stream ID.
            next_oldest = string.match(entry[1], "^([0-9]+)%-") or ""
            if not canonical_uint(next_oldest) then return fail("invalid_next_stream_id") end
            break
        end
    end
end
redis.call(
    "HSET", KEYS[1],
    "archive_ack_sequence", newest,
    "unarchived_count", remaining,
    "oldest_unarchived_at_ms", next_oldest
)
local ids = {}
for index = 4, #ARGV do table.insert(ids, ARGV[index]) end
if #ids > 0 then redis.call("XACK", KEYS[2], group, unpack(ids)) end
return {"ACKNOWLEDGED", newest}
