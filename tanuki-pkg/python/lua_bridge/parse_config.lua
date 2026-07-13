local path = arg[1]
if not path then return end

local f, e = io.open(path, "r")
if not f then return end
local code = f:read("*a")
f:close()

local fn, load_err = load(code, "@" .. path)
if not fn then return end

local ok, err = pcall(fn)
if not ok then return end

function esc(s)
    s = s:gsub("\\", "\\\\")
    s = s:gsub('"', '\\"')
    s = s:gsub("\n", "\\n")
    return s
end

local keys = { "mirror", "suite", "root", "arch", "components", "architectures" }
for _, k in ipairs(keys) do
    local v = _G[k]
    if v ~= nil then
        local t = type(v)
        if t == "string" then
            io.write(k, ' = "', esc(v), '"\n')
        elseif t == "table" then
            local items = {}
            for _, item in ipairs(v) do
                table.insert(items, '"' .. esc(tostring(item)) .. '"')
            end
            io.write(k, " = {", table.concat(items, ", "), "}\n")
        else
            io.write(k, ' = "', esc(tostring(v)), '"\n')
        end
    end
end
