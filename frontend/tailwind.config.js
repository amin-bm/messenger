/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    "../templates/**/*.html",
    "../**/templates/**/*.html",
    "../**/*.html",
    "../static/**/*.js",
    "../**/*.py",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
