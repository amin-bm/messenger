/** @type {import('tailwindcss').Config} */
module.exports = {
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
