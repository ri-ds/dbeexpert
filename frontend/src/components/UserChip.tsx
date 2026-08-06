import { LogoutIcon, UserIcon } from './Icons';

export interface UserChipProps {
  /** Display name from the CCHMC sign in, for example "Rohan Pothuru". */
  name: string;
  /**
   * Where to sign out. When null the button is not rendered at all, because a
   * logout link that goes nowhere is worse than no logout link.
   */
  logoutUrl: string | null;
}

/**
 * The signed in user, top right.
 *
 * This takes the slot the connection pill used to occupy. That pill mostly said
 * "Connected", which is reassurance rather than information, and the app already
 * raises a banner when the graph or the model is actually unreachable. Whose
 * account you are in is the more useful thing to show, especially on a shared PC
 * where the whole point is that history is per person.
 *
 * The connection pill is still rendered alongside this whenever health is not
 * ok, so a real problem is never hidden by the change.
 */
export default function UserChip({ name, logoutUrl }: UserChipProps) {
  return (
    <div className="userchip">
      <span className="userchip__badge" title={`Signed in as ${name}`}>
        <span className="userchip__icon" aria-hidden="true">
          <UserIcon size={13} />
        </span>
        <span className="userchip__name">{name}</span>
      </span>

      {logoutUrl ? (
        // A plain link, not a fetch. Signing out of a SAML session means visiting
        // the service provider's logout URL and following its redirects, which is
        // a navigation, not an API call.
        <a
          className="userchip__out"
          href={logoutUrl}
          title="Sign out"
          aria-label={`Sign out of ${name}`}
        >
          <LogoutIcon size={14} />
          <span className="userchip__out-label">Sign out</span>
        </a>
      ) : null}
    </div>
  );
}
