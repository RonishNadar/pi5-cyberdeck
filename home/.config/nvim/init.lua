-- Basic settings
vim.g.mapleader = " "
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.mouse = "a"              -- mouse and touch: tap to place cursor, drag to select
vim.opt.termguicolors = true
vim.opt.tabstop = 4
vim.opt.shiftwidth = 4
vim.opt.expandtab = true
vim.opt.smartindent = true
vim.opt.wrap = false
vim.opt.cursorline = true
vim.opt.scrolloff = 6
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.undofile = true
vim.opt.signcolumn = "yes"
vim.opt.showmode = false

-- Plugin manager (lazy.nvim), installs itself on first run
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.loop.fs_stat(lazypath) then
  vim.fn.system({ "git", "clone", "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git", "--branch=stable", lazypath })
end
vim.opt.rtp:prepend(lazypath)

require("lazy").setup({
  { "Mofiqul/dracula.nvim", priority = 1000,
    config = function() vim.cmd.colorscheme("dracula") end },

  { "nvim-lualine/lualine.nvim",
    config = function() require("lualine").setup({ options = { theme = "dracula-nvim" } }) end },

  { "nvim-treesitter/nvim-treesitter", branch = "master", build = ":TSUpdate",
    config = function()
      local ok, ts = pcall(require, "nvim-treesitter.configs")
      if not ok then return end
      ts.setup({
        ensure_installed = { "lua", "python", "bash", "c", "cpp", "json", "yaml", "toml", "markdown" },
        highlight = { enable = true },
        indent = { enable = true },
      })
    end },

  { "folke/which-key.nvim", event = "VeryLazy", opts = {} },
}, { ui = { border = "rounded" } })

-- Handy keys
vim.keymap.set("n", "<leader>w", ":w<CR>", { desc = "Save" })
vim.keymap.set("n", "<leader>q", ":q<CR>", { desc = "Quit" })
vim.keymap.set("n", "<Esc>", ":nohlsearch<CR>", { silent = true })
