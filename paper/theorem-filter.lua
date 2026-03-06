-- theorem-filter.lua
-- Pandoc Lua filter:
-- 1. Converts **Theorem X.Y** (Name). *body* into amsthm environments
-- 2. Converts *Proof.* / *Proof sketch.* paragraphs into \begin{proof}
-- 3. Converts <!-- APPENDIX_MARKER --> into \appendix
-- 4. Wraps inline `code` Lean references in \texttt with line-break hints

local env_map = {
  ["Theorem"]     = "theorem",
  ["Lemma"]       = "lemma",
  ["Proposition"] = "proposition",
  ["Corollary"]   = "corollary",
  ["Definition"]  = "definition",
  ["Example"]     = "example",
  ["Remark"]      = "remark",
}

local function parse_strong(elem)
  if elem.t ~= "Strong" then return nil end
  local text = pandoc.utils.stringify(elem)
  for keyword, env in pairs(env_map) do
    local label = text:match("^" .. keyword .. "%s+(.+)$")
    if label then
      return env, label
    end
    if text == keyword then
      return env, nil
    end
  end
  return nil
end

local function extract_opt_name(inlines, start_idx)
  local i = start_idx
  while i <= #inlines do
    local el = inlines[i]
    if el.t == "Space" or el.t == "SoftBreak" then
      i = i + 1
    else
      break
    end
  end

  if i > #inlines then return nil, start_idx end

  local el = inlines[i]
  if el.t ~= "Str" then return nil, start_idx end

  local text = el.text
  if not text:match("^%(") then return nil, start_idx end

  local name_parts = {}
  local found_close = false
  local close_idx = i

  local after_open = text:sub(2)

  local close_pos = after_open:find("%)")
  if close_pos then
    local name_text = after_open:sub(1, close_pos - 1)
    if #name_text > 0 then
      table.insert(name_parts, name_text)
    end
    found_close = true
    close_idx = i + 1
    while close_idx <= #inlines do
      local nel = inlines[close_idx]
      if nel.t == "Str" and (nel.text == "." or nel.text == ")." or nel.text:match("^%.")) then
        close_idx = close_idx + 1
        break
      elseif nel.t == "Space" or nel.t == "SoftBreak" then
        close_idx = close_idx + 1
      else
        break
      end
    end
  else
    if #after_open > 0 then
      table.insert(name_parts, after_open)
    end
    local j = i + 1
    while j <= #inlines do
      local jel = inlines[j]
      if jel.t == "Str" then
        local cp = jel.text:find("%)")
        if cp then
          local before = jel.text:sub(1, cp - 1)
          if #before > 0 then
            table.insert(name_parts, before)
          end
          found_close = true
          close_idx = j + 1
          while close_idx <= #inlines do
            local nel = inlines[close_idx]
            if nel.t == "Str" and (nel.text == "." or nel.text:match("^%.")) then
              close_idx = close_idx + 1
              break
            elseif nel.t == "Space" or nel.t == "SoftBreak" then
              close_idx = close_idx + 1
            else
              break
            end
          end
          break
        else
          table.insert(name_parts, jel.text)
        end
      elseif jel.t == "Space" then
        table.insert(name_parts, " ")
      elseif jel.t == "SoftBreak" then
        table.insert(name_parts, " ")
      else
        table.insert(name_parts, pandoc.utils.stringify(jel))
      end
      j = j + 1
    end
  end

  if found_close then
    local name = table.concat(name_parts)
    return name, close_idx
  else
    return nil, start_idx
  end
end

-- Check if a paragraph starts with *Proof.* or *Proof sketch.*
local function is_proof_start(el)
  local inlines = el.content
  if #inlines < 1 then return false, nil end
  if inlines[1].t ~= "Emph" then return false, nil end
  local emph_text = pandoc.utils.stringify(inlines[1])
  if emph_text == "Proof." then
    return true, nil
  end
  local sketch = emph_text:match("^Proof sketch%.$")
  if sketch then
    return true, "Proof sketch"
  end
  -- "Proof." with text after
  if emph_text:match("^Proof%.") then
    return true, nil
  end
  return false, nil
end

function Para(el)
  local inlines = el.content
  if #inlines < 1 then return nil end

  -- Check for proof paragraphs first
  local is_proof, proof_name = is_proof_start(el)
  if is_proof then
    -- Collect body: everything after the "Proof." emph
    local body_inlines = {}
    for i = 2, #inlines do
      table.insert(body_inlines, inlines[i])
    end
    -- Check if it ends with □ and remove it (amsthm adds its own)
    if #body_inlines > 0 then
      local last = body_inlines[#body_inlines]
      if last.t == "Str" and (last.text == "□" or last.text:match("□$")) then
        local cleaned = last.text:gsub("□", ""):gsub("%s+$", "")
        if #cleaned > 0 then
          body_inlines[#body_inlines] = pandoc.Str(cleaned)
        else
          table.remove(body_inlines, #body_inlines)
          -- Also remove trailing space
          if #body_inlines > 0 and body_inlines[#body_inlines].t == "Space" then
            table.remove(body_inlines, #body_inlines)
          end
        end
      end
    end
    local open_proof
    if proof_name then
      open_proof = string.format("\\begin{proof}[%s]", proof_name)
    else
      open_proof = "\\begin{proof}"
    end
    return {
      pandoc.RawBlock("latex", open_proof),
      pandoc.Para(body_inlines),
      pandoc.RawBlock("latex", "\\end{proof}"),
    }
  end

  -- Check for theorem environments
  local env, label = parse_strong(inlines[1])
  if not env then return nil end

  local opt_name, body_start = extract_opt_name(inlines, 2)

  while body_start <= #inlines do
    local bel = inlines[body_start]
    if bel.t == "Space" or bel.t == "SoftBreak" then
      body_start = body_start + 1
    else
      break
    end
  end

  local body_inlines = {}
  for i = body_start, #inlines do
    table.insert(body_inlines, inlines[i])
  end

  if #body_inlines == 1 and body_inlines[1].t == "Emph" then
    body_inlines = body_inlines[1].content
  end

  local open_env
  if opt_name and #opt_name > 0 then
    open_env = string.format("\\begin{%s}[%s]", env, opt_name)
  else
    open_env = string.format("\\begin{%s}", env)
  end
  local close_env = string.format("\\end{%s}", env)

  return {
    pandoc.RawBlock("latex", open_env),
    pandoc.Para(body_inlines),
    pandoc.RawBlock("latex", close_env),
  }
end

-- Convert <!-- APPENDIX_MARKER --> to \appendix
function RawBlock(el)
  if el.format == "html" and el.text:match("APPENDIX_MARKER") then
    return pandoc.RawBlock("latex", "\\appendix")
  end
end
