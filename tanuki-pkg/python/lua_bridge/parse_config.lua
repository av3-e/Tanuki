local config_path = arg[1]
if not config_path then
    return
end

local f, err = io.open(config_path, "r")
if not f then
    return
end
local code = f:read("*a")
f:close()

local fn, load_err = load(code, "@" .. config_path)
if not fn then
    return
end

local pcall_ok, pcall_err = pcall(fn)
if not pcall_ok then
    return
end

local function escape(s)
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
            io.write(k, ' = "', escape(v), '"\n')
        elseif t == "table" then
            local items = {}
            for _, item in ipairs(v) do
                table.insert(items, '"' .. escape(tostring(item)) .. '"')
            end
            io.write(k, " = {", table.concat(items, ", "), "}\n")
        else
            io.write(k, ' = "', escape(tostring(v)), '"\n')
        end
    end
end
