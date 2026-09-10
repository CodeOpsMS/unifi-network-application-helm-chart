// Run with load("examples/create-mongodb-user.js") in an authenticated mongosh.
// Change the non-secret user/database names to match your deployment first.
// passwordPrompt() takes the ORIGINAL password, not a URI-encoded value.
// This creates one new application user. It does not change an existing user.
const unifiApplicationUser = "unifi";
const unifiDatabaseName = "unifi";
const unifiAuthenticationDatabase = "admin";

db.getSiblingDB(unifiAuthenticationDatabase).createUser({
  user: unifiApplicationUser,
  pwd: passwordPrompt(),
  roles: [
    { role: "clusterMonitor", db: "admin" },
    { role: "dbOwner", db: unifiDatabaseName },
    { role: "dbOwner", db: `${unifiDatabaseName}_stat` },
    { role: "dbOwner", db: `${unifiDatabaseName}_audit` },
    { role: "dbOwner", db: `${unifiDatabaseName}_restore` },
  ],
});
