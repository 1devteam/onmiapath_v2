-- Atomic economy mutation, schema version 1.
--
-- All authoritative integers remain canonical decimal strings. This script
-- never converts balances, amounts, totals, or sequences to Lua numbers.

local INT64_MAX = "9223372036854775807"
local INT64_MIN_ABS = "9223372036854775808"

local function reject(code, reason)
    return {code, reason}
end

local function is_hex64(value)
    return type(value) == "string"
        and string.len(value) == 64
        and string.match(value, "^[0-9a-f]+$") ~= nil
end

local function is_uuid(value)
    return type(value) == "string"
        and string.match(
            value,
            "^[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]%-"
                .. "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]%-"
                .. "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]%-"
                .. "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]%-"
                .. "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
                .. "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]$"
        ) ~= nil
end

local function is_canonical_unsigned(value)
    if type(value) ~= "string" or string.match(value, "^[0-9]+$") == nil then
        return false
    end
    return value == "0" or string.sub(value, 1, 1) ~= "0"
end

local function is_canonical_signed(value)
    if is_canonical_unsigned(value) then
        return true
    end
    if type(value) ~= "string" or string.sub(value, 1, 1) ~= "-" then
        return false
    end
    local magnitude = string.sub(value, 2)
    return magnitude ~= "0"
        and is_canonical_unsigned(magnitude)
end

local function unsigned_compare(left, right)
    if string.len(left) < string.len(right) then
        return -1
    end
    if string.len(left) > string.len(right) then
        return 1
    end
    if left < right then
        return -1
    end
    if left > right then
        return 1
    end
    return 0
end

local function fits_int64(value)
    if not is_canonical_signed(value) then
        return false
    end
    if string.sub(value, 1, 1) == "-" then
        return unsigned_compare(string.sub(value, 2), INT64_MIN_ABS) <= 0
    end
    return unsigned_compare(value, INT64_MAX) <= 0
end

local function is_nonnegative_int64(value)
    return fits_int64(value) and string.sub(value, 1, 1) ~= "-"
end

local function is_positive_int64(value)
    return is_nonnegative_int64(value) and value ~= "0"
end

local function strip_zeroes(value)
    local stripped = string.gsub(value, "^0+", "")
    if stripped == "" then
        return "0"
    end
    return stripped
end

local function unsigned_add(left, right)
    local left_index = string.len(left)
    local right_index = string.len(right)
    local carry = 0
    local output = {}

    while left_index > 0 or right_index > 0 or carry > 0 do
        local left_digit = 0
        local right_digit = 0
        if left_index > 0 then
            left_digit = string.byte(left, left_index) - 48
            left_index = left_index - 1
        end
        if right_index > 0 then
            right_digit = string.byte(right, right_index) - 48
            right_index = right_index - 1
        end
        local total = left_digit + right_digit + carry
        carry = math.floor(total / 10)
        table.insert(output, 1, string.char(48 + (total % 10)))
    end
    return strip_zeroes(table.concat(output))
end

local function unsigned_subtract(left, right)
    -- Requires left >= right.
    local left_index = string.len(left)
    local right_index = string.len(right)
    local borrow = 0
    local output = {}

    while left_index > 0 do
        local left_digit = string.byte(left, left_index) - 48 - borrow
        local right_digit = 0
        if right_index > 0 then
            right_digit = string.byte(right, right_index) - 48
            right_index = right_index - 1
        end
        if left_digit < right_digit then
            left_digit = left_digit + 10
            borrow = 1
        else
            borrow = 0
        end
        table.insert(output, 1, string.char(48 + left_digit - right_digit))
        left_index = left_index - 1
    end
    return strip_zeroes(table.concat(output))
end

local function split_signed(value)
    if string.sub(value, 1, 1) == "-" then
        return -1, string.sub(value, 2)
    end
    return 1, value
end

local function signed_add(left, right)
    local left_sign, left_abs = split_signed(left)
    local right_sign, right_abs = split_signed(right)
    local result

    if left_sign == right_sign then
        result = unsigned_add(left_abs, right_abs)
        if left_sign < 0 and result ~= "0" then
            result = "-" .. result
        end
    else
        local comparison = unsigned_compare(left_abs, right_abs)
        if comparison == 0 then
            result = "0"
        elseif comparison > 0 then
            result = unsigned_subtract(left_abs, right_abs)
            if left_sign < 0 then
                result = "-" .. result
            end
        else
            result = unsigned_subtract(right_abs, left_abs)
            if right_sign < 0 then
                result = "-" .. result
            end
        end
    end

    if not fits_int64(result) then
        return nil
    end
    return result
end

local function negate(value)
    if value == "0" then
        return "0"
    end
    if string.sub(value, 1, 1) == "-" then
        return string.sub(value, 2)
    end
    return "-" .. value
end

local function json_string_value(value)
    if type(value) ~= "string" or string.sub(value, 1, 1) ~= '"' then
        return nil
    end
    local ok, decoded = pcall(cjson.decode, value)
    if not ok or type(decoded) ~= "string" then
        return nil
    end
    return decoded
end

local function has_forbidden_identifier_byte(value)
    for index = 1, string.len(value) do
        local byte = string.byte(value, index)
        if byte <= 31 or byte == 127 or byte == 47 or byte == 92 or byte == 123 or byte == 125 then
            return true
        end
    end
    return false
end

local function is_identifier(value)
    return type(value) == "string"
        and string.len(value) >= 1
        and string.len(value) <= 128
        and string.match(value, "^%s") == nil
        and string.match(value, "%s$") == nil
        and not has_forbidden_identifier_byte(value)
end

local function is_label(value, maximum_length)
    return type(value) == "string"
        and string.len(value) >= 1
        and string.len(value) <= maximum_length
        and string.match(value, "^[a-z][a-z0-9_.:%-]*$") ~= nil
end

local function is_utc_timestamp(value)
    return type(value) == "string"
        and string.match(
            value,
            "^%d%d%d%d%-%d%d%-%d%dT%d%d:%d%d:%d%d%.%d%d%d%d%d%dZ$"
        ) ~= nil
end

local function key_type_is(key, expected)
    local actual = redis.call("TYPE", key)["ok"]
    return actual == "none" or actual == expected
end

local function hash_tag(key)
    return string.match(key, "(%b{})")
end

local function build_record(
    agent_json,
    amount,
    balance_after,
    balance_before,
    created_json,
    delta,
    idempotency_hash,
    mission_json,
    operation_json,
    sequence,
    reason_json,
    request_hash,
    resource_json,
    schema_version,
    tenant_json,
    transaction_json
)
    return '{"agent_id":' .. agent_json
        .. ',"amount_microcredits":' .. amount
        .. ',"balance_after_microcredits":' .. balance_after
        .. ',"balance_before_microcredits":' .. balance_before
        .. ',"created_at":' .. created_json
        .. ',"delta_microcredits":' .. delta
        .. ',"idempotency_key_hash":"' .. idempotency_hash .. '"'
        .. ',"mission_id":' .. mission_json
        .. ',"operation":' .. operation_json
        .. ',"outbox_sequence":' .. sequence
        .. ',"reason":' .. reason_json
        .. ',"request_hash":"' .. request_hash .. '"'
        .. ',"resource_type":' .. resource_json
        .. ',"schema_version":' .. schema_version
        .. ',"tenant_id":' .. tenant_json
        .. ',"transaction_id":' .. transaction_json
        .. '}'
end

if #KEYS ~= 9 or #ARGV ~= 24 then
    return reject("INVALID_ARGUMENT", "abi_arity")
end

local expected_tag = hash_tag(KEYS[1])
if expected_tag == nil then
    return reject("INVALID_ARGUMENT", "missing_hash_tag")
end
for index = 2, 9 do
    if hash_tag(KEYS[index]) ~= expected_tag then
        return reject("INVALID_ARGUMENT", "cross_slot_keys")
    end
end

local expected_types = {
    "hash", "set", "hash", "stream", "stream", "stream", "hash", "hash", "string"
}
for index = 1, 9 do
    if not key_type_is(KEYS[index], expected_types[index]) then
        return reject("CORRUPT_STATE", "wrong_key_type")
    end
end

local schema_version = ARGV[1]
local operation = ARGV[2]
local amount = ARGV[3]
local agent_id = ARGV[4]
local agent_json = ARGV[5]
local agent_type = ARGV[6]
local resource_json = ARGV[7]
local reason_json = ARGV[8]
local mission_json = ARGV[9]
local tenant_json = ARGV[10]
local request_hash = ARGV[11]
local idempotency_hash = ARGV[12]
local transaction_id = ARGV[13]
local transaction_json = ARGV[14]
local transaction_created_json = ARGV[15]
local opening_transaction_id = ARGV[16]
local opening_transaction_json = ARGV[17]
local opening_created_json = ARGV[18]
local opening_idempotency_hash = ARGV[19]
local opening_request_hash = ARGV[20]
local opening_grant = ARGV[21]
local archive_hard_records = ARGV[22]
local archive_hard_age_ms = ARGV[23]
local now_epoch_ms = ARGV[24]

if schema_version ~= "1" then
    return reject("SCHEMA_MISMATCH", "unsupported_argument_schema")
end
if operation ~= "charge" and operation ~= "reward" then
    return reject("INVALID_ARGUMENT", "unsupported_operation")
end
if not is_positive_int64(amount)
    or not is_positive_int64(opening_grant)
    or not is_positive_int64(archive_hard_records)
    or not is_positive_int64(archive_hard_age_ms)
    or not is_nonnegative_int64(now_epoch_ms)
then
    return reject("INVALID_ARGUMENT", "invalid_integer_argument")
end
if not is_hex64(request_hash)
    or not is_hex64(idempotency_hash)
    or not is_hex64(opening_idempotency_hash)
    or not is_hex64(opening_request_hash)
then
    return reject("INVALID_ARGUMENT", "invalid_hash_argument")
end
if not is_uuid(transaction_id) or not is_uuid(opening_transaction_id) then
    return reject("INVALID_ARGUMENT", "invalid_transaction_id")
end
local decoded_agent = json_string_value(agent_json)
local decoded_resource = json_string_value(resource_json)
local decoded_reason = json_string_value(reason_json)
local decoded_tenant = json_string_value(tenant_json)
local decoded_transaction_id = json_string_value(transaction_json)
local decoded_transaction_created = json_string_value(transaction_created_json)
local decoded_opening_transaction_id = json_string_value(opening_transaction_json)
local decoded_opening_created = json_string_value(opening_created_json)
local decoded_mission = nil
if mission_json ~= "null" then
    decoded_mission = json_string_value(mission_json)
end

if not is_identifier(agent_id)
    or decoded_agent ~= agent_id
    or not is_identifier(decoded_tenant)
    or not is_label(agent_type, 64)
    or not is_label(decoded_resource, 64)
    or not is_label(decoded_reason, 128)
    or (mission_json ~= "null" and not is_identifier(decoded_mission))
then
    return reject("INVALID_ARGUMENT", "invalid_agent_argument")
end
if decoded_transaction_id ~= transaction_id
    or decoded_opening_transaction_id ~= opening_transaction_id
    or not is_utc_timestamp(decoded_transaction_created)
    or not is_utc_timestamp(decoded_opening_created)
then
    return reject("INVALID_ARGUMENT", "invalid_json_string")
end

if redis.call("EXISTS", KEYS[9]) == 1 then
    return reject("MIGRATION_LOCKED", "migration_lock_active")
end
if redis.call("HEXISTS", KEYS[8], agent_id) == 1 then
    return reject("ACCOUNT_QUARANTINED", "account_quarantined")
end

local meta_exists = redis.call("EXISTS", KEYS[1]) == 1
local next_sequence = "1"
local unarchived_count = "0"
local oldest_unarchived_at_ms = ""
local archive_state = "healthy"
local memory_state = "healthy"

if meta_exists then
    local meta_schema = redis.call("HGET", KEYS[1], "schema_version")
    if meta_schema ~= schema_version then
        return reject("SCHEMA_MISMATCH", "meta_schema_mismatch")
    end
    next_sequence = redis.call("HGET", KEYS[1], "next_sequence")
    unarchived_count = redis.call("HGET", KEYS[1], "unarchived_count")
    oldest_unarchived_at_ms = redis.call("HGET", KEYS[1], "oldest_unarchived_at_ms")
    archive_state = redis.call("HGET", KEYS[1], "archive_state")
    memory_state = redis.call("HGET", KEYS[1], "memory_state")
    local archive_ack_sequence = redis.call("HGET", KEYS[1], "archive_ack_sequence")
    local circuit_observed_at_ms = redis.call("HGET", KEYS[1], "circuit_observed_at_ms")

    if not is_positive_int64(next_sequence)
        or not is_nonnegative_int64(unarchived_count)
        or not is_nonnegative_int64(archive_ack_sequence)
        or not is_nonnegative_int64(circuit_observed_at_ms)
        or (oldest_unarchived_at_ms ~= "" and not is_nonnegative_int64(oldest_unarchived_at_ms))
        or (archive_state ~= "healthy" and archive_state ~= "warning"
            and archive_state ~= "stopped" and archive_state ~= "corrupt")
        or (memory_state ~= "healthy" and memory_state ~= "stopped")
    then
        return reject("CORRUPT_STATE", "invalid_meta_state")
    end
    if (unarchived_count == "0" and oldest_unarchived_at_ms ~= "")
        or (unarchived_count ~= "0" and oldest_unarchived_at_ms == "")
    then
        return reject("CORRUPT_STATE", "invalid_archive_oldest_state")
    end
    local next_minus_ack = signed_add(next_sequence, negate(archive_ack_sequence))
    local expected_unarchived = next_minus_ack ~= nil
        and signed_add(next_minus_ack, "-1") or nil
    if expected_unarchived == nil or expected_unarchived ~= unarchived_count then
        return reject("CORRUPT_STATE", "broken_archive_count_equation")
    end
end

if archive_state == "stopped" or archive_state == "corrupt" then
    return reject("ARCHIVE_LAG_LIMIT", "archive_circuit_open")
end
if memory_state == "stopped" then
    return reject("ARCHIVE_LAG_LIMIT", "memory_circuit_open")
end
if unsigned_compare(unarchived_count, archive_hard_records) >= 0 then
    return reject("ARCHIVE_LAG_LIMIT", "archive_record_limit")
end
if oldest_unarchived_at_ms ~= "" then
    local oldest_age = signed_add(now_epoch_ms, negate(oldest_unarchived_at_ms))
    if oldest_age == nil or string.sub(oldest_age, 1, 1) == "-" then
        return reject("CORRUPT_STATE", "invalid_archive_clock")
    end
    if unsigned_compare(oldest_age, archive_hard_age_ms) >= 0 then
        return reject("ARCHIVE_LAG_LIMIT", "archive_age_limit")
    end
end

if redis.call("EXISTS", KEYS[7]) == 1 then
    local idem_schema = redis.call("HGET", KEYS[7], "schema_version")
    local stored_request_hash = redis.call("HGET", KEYS[7], "request_hash")
    local result_json = redis.call("HGET", KEYS[7], "result_json")
    local stored_transaction_id = redis.call("HGET", KEYS[7], "transaction_id")
    local stored_transaction_sequence = redis.call("HGET", KEYS[7], "transaction_sequence")
    local stored_opening_transaction_id = redis.call(
        "HGET", KEYS[7], "opening_transaction_id"
    )
    local stored_created_at = redis.call("HGET", KEYS[7], "created_at")
    if idem_schema ~= schema_version or not is_hex64(stored_request_hash)
        or not is_uuid(stored_transaction_id)
        or not is_positive_int64(stored_transaction_sequence)
        or (stored_opening_transaction_id ~= ""
            and not is_uuid(stored_opening_transaction_id))
        or not is_utc_timestamp(stored_created_at)
        or type(result_json) ~= "string" or result_json == ""
    then
        return reject("CORRUPT_STATE", "invalid_idempotency_state")
    end
    if stored_request_hash == request_hash then
        return {"REPLAYED", result_json}
    end
    return reject("IDEMPOTENCY_CONFLICT", "idempotency_key_reused")
end

local balance_exists = redis.call("EXISTS", KEYS[3]) == 1
local balance_before = "0"
local total_earned = "0"
local total_spent = "0"
local opening_sequence = nil
local requested_sequence = next_sequence

if balance_exists then
    local balance_schema = redis.call("HGET", KEYS[3], "schema_version")
    local stored_agent_id = redis.call("HGET", KEYS[3], "agent_id")
    local stored_agent_type = redis.call("HGET", KEYS[3], "agent_type")
    balance_before = redis.call("HGET", KEYS[3], "balance_microcredits")
    total_earned = redis.call("HGET", KEYS[3], "total_earned_microcredits")
    total_spent = redis.call("HGET", KEYS[3], "total_spent_microcredits")
    local last_sequence = redis.call("HGET", KEYS[3], "last_sequence")
    local last_transaction_id = redis.call("HGET", KEYS[3], "last_transaction_id")
    local updated_at = redis.call("HGET", KEYS[3], "updated_at")

    if balance_schema ~= schema_version then
        return reject("SCHEMA_MISMATCH", "balance_schema_mismatch")
    end
    if stored_agent_id ~= agent_id
        or not is_label(stored_agent_type, 64)
        or not fits_int64(balance_before)
        or not is_nonnegative_int64(total_earned)
        or not is_nonnegative_int64(total_spent)
        or not is_positive_int64(last_sequence)
        or not is_uuid(last_transaction_id)
        or type(updated_at) ~= "string" or updated_at == ""
    then
        return reject("CORRUPT_STATE", "invalid_balance_state")
    end
    if unsigned_compare(last_sequence, next_sequence) >= 0 then
        return reject("CORRUPT_STATE", "invalid_balance_sequence")
    end
    local expected_balance = signed_add(total_earned, negate(total_spent))
    if expected_balance == nil or expected_balance ~= balance_before then
        return reject("CORRUPT_STATE", "broken_balance_equation")
    end
else
    opening_sequence = next_sequence
    requested_sequence = signed_add(next_sequence, "1")
    if requested_sequence == nil then
        return reject("INTEGER_OVERFLOW", "sequence_overflow")
    end
    balance_before = opening_grant
    total_earned = opening_grant
end

local delta = amount
if operation == "charge" then
    delta = "-" .. amount
end
local balance_after = signed_add(balance_before, delta)
if balance_after == nil then
    return reject("INTEGER_OVERFLOW", "balance_overflow")
end
if operation == "charge" and string.sub(balance_after, 1, 1) == "-" then
    return reject("INSUFFICIENT_FUNDS", "insufficient_funds")
end

local new_total_earned = total_earned
local new_total_spent = total_spent
if operation == "reward" then
    new_total_earned = signed_add(total_earned, amount)
    if new_total_earned == nil then
        return reject("INTEGER_OVERFLOW", "earned_total_overflow")
    end
else
    new_total_spent = signed_add(total_spent, amount)
    if new_total_spent == nil then
        return reject("INTEGER_OVERFLOW", "spent_total_overflow")
    end
end

local new_next_sequence = signed_add(requested_sequence, "1")
if new_next_sequence == nil then
    return reject("INTEGER_OVERFLOW", "sequence_overflow")
end
local record_count = "1"
if opening_sequence ~= nil then
    record_count = "2"
end
local new_unarchived_count = signed_add(unarchived_count, record_count)
if new_unarchived_count == nil then
    return reject("INTEGER_OVERFLOW", "outbox_count_overflow")
end
if unsigned_compare(new_unarchived_count, archive_hard_records) > 0 then
    return reject("ARCHIVE_LAG_LIMIT", "archive_record_limit")
end

local operation_json = '"reward"'
if operation == "charge" then
    operation_json = '"charge"'
end
local requested_record = build_record(
    agent_json,
    amount,
    balance_after,
    balance_before,
    transaction_created_json,
    delta,
    idempotency_hash,
    mission_json,
    operation_json,
    requested_sequence,
    reason_json,
    request_hash,
    resource_json,
    schema_version,
    tenant_json,
    transaction_json
)

local opening_record = nil
if opening_sequence ~= nil then
    opening_record = build_record(
        agent_json,
        opening_grant,
        opening_grant,
        "0",
        opening_created_json,
        opening_grant,
        opening_idempotency_hash,
        "null",
        '"opening_grant"',
        opening_sequence,
        '"opening_grant"',
        opening_request_hash,
        '"opening_grant"',
        schema_version,
        tenant_json,
        opening_transaction_json
    )
end

local result_json = '{"balance_microcredits":' .. balance_after
    .. ',"opening_transaction":'
if opening_record == nil then
    result_json = result_json .. "null"
else
    result_json = result_json .. opening_record
end
result_json = result_json .. ',"transaction":' .. requested_record .. '}'

-- All validation and every potentially failing arithmetic operation is complete.
redis.call(
    "HSET",
    KEYS[1],
    "schema_version", schema_version,
    "next_sequence", new_next_sequence,
    "archive_ack_sequence", meta_exists
        and redis.call("HGET", KEYS[1], "archive_ack_sequence") or "0",
    "unarchived_count", new_unarchived_count,
    "oldest_unarchived_at_ms", unarchived_count == "0"
        and now_epoch_ms or oldest_unarchived_at_ms,
    "archive_state", archive_state,
    "memory_state", memory_state,
    "circuit_observed_at_ms", meta_exists
        and redis.call("HGET", KEYS[1], "circuit_observed_at_ms") or now_epoch_ms
)
redis.call(
    "HSET",
    KEYS[3],
    "schema_version", schema_version,
    "agent_id", agent_id,
    "agent_type", agent_type,
    "balance_microcredits", balance_after,
    "total_earned_microcredits", new_total_earned,
    "total_spent_microcredits", new_total_spent,
    "last_sequence", requested_sequence,
    "last_transaction_id", transaction_id,
    "updated_at", cjson.decode(transaction_created_json)
)
redis.call("SADD", KEYS[2], agent_id)

if opening_record ~= nil then
    redis.call(
        "XADD", KEYS[4], "*",
        "sequence", opening_sequence,
        "transaction_id", opening_transaction_id,
        "record_json", opening_record
    )
    redis.call(
        "XADD", KEYS[5], "*",
        "sequence", opening_sequence,
        "transaction_id", opening_transaction_id,
        "record_json", opening_record
    )
    redis.call(
        "XADD", KEYS[6], "*",
        "sequence", opening_sequence,
        "transaction_id", opening_transaction_id,
        "record_json", opening_record
    )
end

redis.call(
    "XADD", KEYS[4], "*",
    "sequence", requested_sequence,
    "transaction_id", transaction_id,
    "record_json", requested_record
)
redis.call(
    "XADD", KEYS[5], "*",
    "sequence", requested_sequence,
    "transaction_id", transaction_id,
    "record_json", requested_record
)
redis.call(
    "XADD", KEYS[6], "*",
    "sequence", requested_sequence,
    "transaction_id", transaction_id,
    "record_json", requested_record
)
redis.call(
    "HSET",
    KEYS[7],
    "schema_version", schema_version,
    "request_hash", request_hash,
    "transaction_id", transaction_id,
    "transaction_sequence", requested_sequence,
    "opening_transaction_id", opening_sequence ~= nil and opening_transaction_id or "",
    "result_json", result_json,
    "created_at", cjson.decode(transaction_created_json)
)

return {"COMMITTED", result_json}
