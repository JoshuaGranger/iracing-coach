using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class Garage61StatusConsumerTests
{
    [TestMethod]
    public void Connected_RequiresAffirmativeEvidenceOnEveryAxis()
    {
        Assert.IsTrue(Status("saved", "valid", "granted", "available").Connected);
        Assert.IsFalse(Status("saved", "unverified", "granted", "available").Connected);
        Assert.IsFalse(Status("saved", "valid", "unverified", "available").Connected);
        Assert.IsFalse(Status("saved", "valid", "granted", "unreachable").Connected);
        Assert.IsFalse(Status("absent", "valid", "granted", "available").Connected);
    }

    [TestMethod]
    public void AvailabilityFailure_PreservesAuthenticationAndPermission()
    {
        var connected = Status("saved", "valid", "granted", "available");

        var timeout = Garage61StatusReducer.ApplyProbe(connected, "timeout");
        var unreachable = Garage61StatusReducer.ApplyProbe(timeout, "dns_failure");

        Assert.AreEqual("valid", unreachable.Authentication);
        Assert.AreEqual("granted", unreachable.Permission);
        Assert.AreEqual("unreachable", unreachable.Availability);
        Assert.IsFalse(unreachable.Connected);
        Assert.AreEqual("retry_later", unreachable.Remedy);
    }

    [TestMethod]
    public void AuthenticationAndPermissionFailures_StayDistinct()
    {
        var unprobed = Garage61StatusReducer.Unprobed(true);

        var unauthorized = Garage61StatusReducer.ApplyProbe(unprobed, "unauthorized");
        var forbidden = Garage61StatusReducer.ApplyProbe(unprobed, "forbidden");
        var scope = Garage61StatusReducer.ApplyProbe(unprobed, "insufficient_scope");

        Assert.AreEqual("rejected", unauthorized.Authentication);
        Assert.AreEqual("replace_credential", unauthorized.Remedy);
        Assert.AreEqual("valid", forbidden.Authentication);
        Assert.AreEqual("denied", forbidden.Permission);
        Assert.AreEqual("check_account_access", forbidden.Remedy);
        Assert.AreEqual("valid", scope.Authentication);
        Assert.AreEqual("insufficient_scope", scope.Permission);
        Assert.AreEqual("grant_scope", scope.Remedy);
    }

    [TestMethod]
    public void RuntimeResponse_RequiresDrivingDataPermissionBeforeConnected()
    {
        using var withoutScope = JsonDocument.Parse("""
            {"ok":true,"configured":true,"status":"available","capabilities":{"driving_data":{"available":false}}}
            """);
        using var withScope = JsonDocument.Parse("""
            {"ok":true,"configured":true,"status":"available","capabilities":{"driving_data":{"available":true}}}
            """);

        var denied = RuntimeMapper.Garage61(withoutScope.RootElement);
        var connected = RuntimeMapper.Garage61(withScope.RootElement);

        Assert.AreEqual("insufficient_scope", denied.Permission);
        Assert.IsFalse(denied.Connected);
        Assert.IsTrue(connected.Connected);
    }

    [TestMethod]
    public void TransportFailure_CannotRetainPriorConnectedClaim()
    {
        var connected = Status("saved", "valid", "granted", "available");
        using var failed = JsonDocument.Parse("""
            {"ok":false,"configured":true,"status":"unavailable","error_type":"Garage61TransportError","message":"offline"}
            """);

        var result = RuntimeMapper.Garage61(failed.RootElement, connected);

        Assert.AreEqual("valid", result.Authentication);
        Assert.AreEqual("granted", result.Permission);
        Assert.AreEqual("unreachable", result.Availability);
        Assert.IsFalse(result.Connected);
    }

    [TestMethod]
    public void ProtectedReplacement_RestoresPreviousBlobUnlessCommitted()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-g61-replacement", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var path = Path.Combine(root, "garage61.dpapi");
            var store = new FileBackedStore(path);
            store.Store("old-protected-blob");

            using (var replacement = ((IGarage61CredentialStore)store).BeginReplacement("candidate-protected-blob"))
            {
                Assert.AreEqual("candidate-protected-blob", File.ReadAllText(path));
            }
            Assert.AreEqual("old-protected-blob", File.ReadAllText(path));

            using (var replacement = ((IGarage61CredentialStore)store).BeginReplacement("accepted-protected-blob"))
            {
                replacement.Commit();
            }
            Assert.AreEqual("accepted-protected-blob", File.ReadAllText(path));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private static Garage61Connection Status(
        string credential,
        string authentication,
        string permission,
        string availability) =>
        new(credential, authentication, permission, availability, "test", "test");

    private sealed class FileBackedStore(string path) : IGarage61CredentialStore
    {
        public bool IsConfigured => File.Exists(CredentialPath);
        public string CredentialPath { get; } = path;
        public void Store(string token) => File.WriteAllText(CredentialPath, token);
        public void Remove()
        {
            if (File.Exists(CredentialPath)) File.Delete(CredentialPath);
        }
    }
}
