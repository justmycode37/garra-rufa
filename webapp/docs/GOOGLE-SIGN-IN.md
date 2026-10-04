# Enable Google sign-in

The site is https://garra-rufa.vercel.app. Email/password sign-in already works;
email confirmation remains disabled as requested. Google sign-in needs your
Google OAuth client credentials in Supabase. It requests identity only, not
access to a Gmail inbox.

1. Open [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients)
   and select or create your Google Cloud project. Configure Branding with the
   app name **Garra Rufa**, your support email and developer contact. Select an
   External audience for public users. While in Testing, add your test Google
   accounts under Audience; publish the OAuth app when ready for everyone.
2. Create an OAuth client of type **Web application**. Add the JavaScript origin
   `https://garra-rufa.vercel.app` and, for local testing, `http://127.0.0.1:3000`.
   Set this exact **Authorized redirect URI**:

   ```text
   https://mhmbyajftzuyqffpajfb.supabase.co/auth/v1/callback
   ```

3. In [Supabase Authentication → Sign In / Providers](https://supabase.com/dashboard/project/mhmbyajftzuyqffpajfb/auth/providers),
   enable Google. Paste the Google client ID and client secret from step 2 and
   save. These values belong in Supabase, never in frontend code or GitHub.
4. In [Supabase Authentication → URL Configuration](https://supabase.com/dashboard/project/mhmbyajftzuyqffpajfb/auth/url-configuration),
   set **Site URL** to `https://garra-rufa.vercel.app`. Add these redirect URLs:

   ```text
   https://garra-rufa.vercel.app/api/auth/callback
   https://garra-rufa.vercel.app/api/auth/callback?recovery=1
   http://127.0.0.1:3000/api/auth/callback
   http://127.0.0.1:3000/api/auth/callback?recovery=1
   ```

5. Refresh the site after about 30 seconds. Choose a role, continue to sign-in,
   and select **Sign in with Google**. The app enables the button automatically
   when Supabase reports Google enabled; no redeployment is required. Confirm
   that it returns to your workspace, then sign out and back in.

Keep email confirmations disabled if that is your chosen policy. Password-reset
emails still depend on Supabase email delivery; configure a custom SMTP sender
for reliable public password resets.

Official references: [Supabase Google provider guide](https://supabase.com/docs/guides/auth/social-login/auth-google),
[redirect URL configuration](https://supabase.com/docs/guides/auth/redirect-urls),
[Google button branding](https://developers.google.com/identity/branding-guidelines).
