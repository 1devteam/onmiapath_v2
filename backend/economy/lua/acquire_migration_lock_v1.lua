-- Acquire or renew a tenant migration lock without stealing another owner.
local current = redis.call("GET", KEYS[1])
if not current then
    if redis.call("SET", KEYS[1], ARGV[1], "NX", "PX", ARGV[2]) then
        return "ACQUIRED"
    end
    return "BUSY"
end
if current == ARGV[1] then
    redis.call("PEXPIRE", KEYS[1], ARGV[2])
    return "RENEWED"
end
return "BUSY"
