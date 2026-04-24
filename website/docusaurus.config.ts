import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// Repo refs — change REPO_URL / EDIT_BRANCH if the repo moves or
// renames its default branch.
const REPO_URL = 'https://github.com/open-labdog/labdog';
const EDIT_BRANCH = 'main';

const config: Config = {
  title: 'LabDog',
  tagline: "A homelabber's best friend",
  favicon: 'img/favicon.ico',

  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  // Production URL — placeholder until the site is actually published.
  url: 'https://labdog.example.com',
  baseUrl: '/',

  // Used by the `npm run deploy` GitHub Pages command if we ever use it.
  organizationName: 'dennis',
  projectName: 'labdog',

  // Fail the build on broken links — the existing content has been
  // audited and verified to resolve against the wrapped docs tree.
  onBrokenLinks: 'throw',

  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          // Point at the repo-root `docs/` directory.  Keeps the
          // content authoritative for both raw repo browsing and the
          // rendered site.
          path: '../docs',
          // Serve docs at the site root (no `/docs/` prefix).
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: `${REPO_URL}/-/edit/${EDIT_BRANCH}/`,
        },
        // No blog.
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/docusaurus-social-card.jpg',
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'LabDog',
      logo: {
        alt: 'LabDog',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'defaultSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: REPO_URL,
          label: 'Source',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Overview', to: '/'},
            {label: 'GitOps guide', to: '/examples/gitops/'},
            {label: 'Precedence', to: '/examples/precedence/'},
          ],
        },
        {
          title: 'Code',
          items: [
            {label: 'Repository', href: REPO_URL},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} LabDog.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'toml', 'python'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
