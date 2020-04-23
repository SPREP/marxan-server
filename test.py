#unit tests for marxan-server.py. 
# To test:
# 1. change to the marxan-server directory
# 2. conda activate base (or the conda environment used to install marxan-server)
# 3. Run the following (the -W option disables all warnings - if you omit it you will see ResourceWarnings for things like Sockets not closing when you start an upload the Mapbox and the unit tests stop)
#    python3 -W ignore -m unittest test -v
# To test against an SSL localhost:
# 1. Replace all AsyncHTTPTestCase with AsyncHTTPSTestCase
# 2. Set TEST_HTTP, TEST_WS and TEST_REFERER to point to secure endpoints, e.g. https and wss
import unittest, importlib, tornado, aiopg, json, urllib, os, sys
from tornado.testing import AsyncHTTPTestCase, gen_test
from tornado.ioloop import IOLoop
from tornado.httpclient import HTTPRequest
from tornado import httputil
from shutil import copyfile

#CONSTANTS
MARXAN_SERVER_FOLDER = os.getcwd()
LOGIN_USER = "admin"
LOGIN_PASSWORD = "password"
TEST_HTTP = "http://localhost"
TEST_WS = "ws://localhost"
TEST_REFERER = "http://localhost"
TEST_USER = "unit_tester"
TEST_PROJECT = "test_project"
TEST_IMPORT_PROJECT = "test_import_project"
TEST_DATA_FOLDER = MARXAN_SERVER_FOLDER + os.sep + "test-data" + os.sep 
TEST_ZIP_SHP_MULTIPLE = "multiple_features.zip"
TEST_ZIP_SHP_INVALID_GEOM = "invalid_geometry.zip"
TEST_ZIP_SHP_MISSING_FILE = "pulayer_missing_file.zip"

#global variables
cookie = None
projects = ''
fcns = []

#import the marxan-server module
m = importlib.import_module("marxan-server")
#set the ASYNC_TEST_TIMEOUT environment variable as described here http://www.tornadoweb.org/en/stable/testing.html#tornado.testing.AsyncTestCase.wait
os.environ['ASYNC_TEST_TIMEOUT'] = '600' # 10 minutes

def initialiseServer():
    m.SHOW_START_LOG = False
    #set the global variables
    m._setGlobalVariables()
    m.DISABLE_SECURITY = False
    m.SHOW_START_LOG = False
    return 
    
def setCookies(response):
    #get the cookies
    cookies = response.headers['set-cookie'].split(",")
    parsed = [httputil.parse_cookie(c) for c in cookies]
    #get the user cookie
    userCookie = next((c for c in parsed if "user" in c.keys()), None)
    #get the role cookie
    roleCookie = next((c for c in parsed if "role" in c.keys()), None)
    global cookie
    cookie = "user=" + userCookie['user'] + ";role=" + roleCookie['role']

def copyTestData(filename):
    """
    Copies a file from the unittests/test-data folder to the marxan-server folder
    
    Arguments:
        filename (str): the name of the file to copy including the extension
    """
    testFile = TEST_DATA_FOLDER + filename
    destFile = MARXAN_SERVER_FOLDER + os.sep + filename
    if os.path.exists(destFile):
        os.remove(destFile)
    copyfile(testFile, destFile)

class TestClass(AsyncHTTPTestCase):
    @gen_test
    def get_app(self):
        #set variables
        initialiseServer()
        #create a connection pool 
        self._pool = yield aiopg.create_pool(m.CONNECTION_STRING, timeout = None)
        #create the app
        self._app = m.Application(self._pool)
        return self._app
    
    @gen_test
    def tearDown(self):
        #free the database connection
        self._pool.close()
        yield self._pool.wait_closed()
        
    def getDictResponse(self, response, mustReturnError):
        """
        Parses the response from either a GET/POST request or a WebSocket message to check for errors
        """
        #set a flag to indicate if the request is complete or not
        requestComplete = False
        if hasattr(response, 'body'):
            #for GET/POST requests there is only one response
            _dict = dict(json.loads(response.body.decode("utf-8"))) 
            requestComplete = True
        else:
            #for WebSocket requests there will be more than one message
            _dict = dict(json.loads(response)) 
            #if the status is Finished then the request is complete
            requestComplete = _dict['status'] == "Finished"
        #print any error messages
        if ('error' in _dict.keys()):
            err = _dict['error']
            #leave out the href link to the error message
            if err.find("See <"):
                err = err[:err.find("See <")]
            print(err, end=' ', flush=True)
        #assertions for errors
        if requestComplete:
            if mustReturnError:
                self.assertTrue('error' in _dict.keys())
            else:
                self.assertFalse('error' in _dict.keys())
        return _dict
    
    @gen_test
    def makeRequest(self, url, mustReturnError, **kwargs):
        """Makes a GET/POST request to the url using the optional kwargs arguments
        
        Parameters:
            url (str): The query part of the url to request - this excludes the /marxan-server prefix
            mustReturnError (bool): If True, the request must return an error and the assertTrue will be run
            **kwargs (optional): Additional parameters that are passed by keyword to the url fetch function, e.g. headers, body
        
        Returns:
            dict: The json response from the marxan-server
        """
        #get any existing headers
        if "headers" in kwargs.keys():
            d1 = kwargs['headers']
        else:
            d1 = {}
        # get the generic headers
        d2 = {'Cookie': cookie, "referer": TEST_REFERER} if cookie else {"referer": TEST_REFERER}
        #merge the headers
        kwargs.update({'headers': {**d1, **d2}})
        #make the request
        response = yield self.http_client.fetch(TEST_HTTP + ':' + str(self.get_http_port()) + "/marxan-server" + url, **kwargs)
        #assert a valid response
        self.assertEqual(response.code, 200)
        #if the response has cookies then set them globally
        if ('set-cookie' in response.headers.keys() and response.headers['set-cookie']):
            setCookies(response)
        # get the response as a dictionary
        _dict = self.getDictResponse(response, mustReturnError)
        return _dict

    @gen_test
    def makeWebSocketRequest(self, request, mustReturnError, **kwargs):
        msgs = []
        # add the cookies if they have been set
        if cookie:
            kwargs.update({'headers':{'Cookie': cookie, "referer": TEST_REFERER}})
        else:
            kwargs.update({'headers':{"referer": TEST_REFERER}})
        #dont attempt to validate the SSL certificate otherwise you get SSL errors - not sure why and set the request timeout (5 seconds by default)
        kwargs.update({'validate_cert': False, 'request_timeout': None})
        #get the websocket url
        ws_url = TEST_WS + ":" + str(self.get_http_port()) + "/marxan-server" + request
        #make the request
        ws_client = yield tornado.websocket.websocket_connect(HTTPRequest(ws_url, **kwargs))
        #log the messages from the websocket
        while True:
            msg = yield ws_client.read_message()
            if not msg:
                break
            _dict = self.getDictResponse(msg, mustReturnError)
            msgs.append(_dict)
            # print(_dict)
        return msgs
    
    def uploadFile(self, fullPath, formData, mustReturnError):
        #get the filename from the full path
        filename = fullPath[fullPath.rfind(os.sep) + 1:]
        boundary = 'SomeRandomBoundary'
        headers = {'Content-Type': 'multipart/form-data; boundary=%s' % boundary}
        body = '--%s\r\n' % boundary 
        #add the form-data to the request
        for k in formData.keys():
            body += 'Content-Disposition: form-data; name="' + k + '"\r\n'
            body += '\r\n' 
            body += formData[k] + "\r\n"
            body += '--%s\r\n' % boundary 
        body += 'Content-Disposition: form-data; name="value"; filename="' + filename + '"\r\n'
        body += 'Content-Type: application/zip\r\n'
        body += '\r\n' # blank line
        with open(fullPath, 'rb') as f:
            body += '%s\r\n' % f.read() #TODO This needs to be written as binary data!
        body += "--%s--\r\n" % boundary
        port = str(self.get_http_port())
        self.makeRequest('/uploadShapefile', mustReturnError, method='POST', headers=headers, body=body)
        
    ###########################################################################
    # start of individual tests
    ###########################################################################

    #synchronous GET request
    def test_0050_validateUser(self):
        self.makeRequest('/validateUser?user=' + LOGIN_USER + '&password=' + LOGIN_PASSWORD, False) 

    def test_0100_getServerData(self):
        self.makeRequest('/getServerData', False)

    def test_0300_createUser(self):
        body = urllib.parse.urlencode({"user":TEST_USER,"password":"wibble","fullname":"wibble","email":"a@b.com"})
        self.makeRequest('/createUser', False, method="POST", body=body)
        
    def test_0600_toggleEnableGuestUser(self):
        _dict = self.makeRequest('/toggleEnableGuestUser', False)
        self.assertTrue('enabled' in _dict.keys())
        
    def test_0800_getProjectsWithGrids(self):
        self.makeRequest('/getProjectsWithGrids', False)

    def test_1000_createProject(self):
        body = urllib.parse.urlencode({"user":TEST_USER,"project":TEST_PROJECT,"description":"whatever","planning_grid_name":"pu_ton_marine_hexagon_50", 'interest_features':'63407942,63408405,63408475,63767166','target_values':'33,17,45,17','spf_values':'40,40,40,40'})
        self.makeRequest('/createProject', False, method="POST", body=body)

    def test_1080_preprocessFeature(self):
        self.makeWebSocketRequest('/preprocessFeature?user=' + TEST_USER + '&project=' + TEST_PROJECT + '&planning_grid_name=pu_ton_marine_hexagon_50&feature_class_name=volcano&alias=volcano&id=63408475', False)

    def test_1085_preprocessPlanningUnits(self):
        self.makeWebSocketRequest('/preprocessPlanningUnits?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1090_preprocessProtectedAreas(self):
        self.makeWebSocketRequest('/preprocessProtectedAreas?user=' + TEST_USER + '&project=' + TEST_PROJECT + '&planning_grid_name=pu_ton_marine_hexagon_50', False)

    def test_1092_updatePUFile(self):
        body = urllib.parse.urlencode({"user":TEST_USER,"project":TEST_PROJECT, "status2": "8172", "status3": "8542,8541"})
        f = self.makeRequest('/updatePUFile', False, method="POST", body=body)

    def test_1093_updateSpecFile(self):
        body = urllib.parse.urlencode({"user":TEST_USER,"project":TEST_PROJECT, "interest_features": "63407942,63408405", "target_values": "52,53", "spf_values":"40,40"})
        self.makeRequest('/updateSpecFile', False, method="POST", body=body)

    def test_1094_runMarxan(self):
        self.makeWebSocketRequest('/runMarxan?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1095_getRunLogs(self):
        self.makeRequest('/getRunLogs?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)
    
    def test_1096_clearRunLogs(self):
        self.makeRequest('/clearRunLogs?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1096_runGapAnalysis(self):
        self.makeWebSocketRequest('/runGapAnalysis?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1097_deleteGapAnalysis(self):
        self.makeRequest('/deleteGapAnalysis?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1100_createImportProject(self):
        body = urllib.parse.urlencode({"user":TEST_USER,"project":TEST_IMPORT_PROJECT})
        self.makeRequest('/createImportProject', False, method="POST", body=body)

    def test_1125_getProjects(self):
        self.makeRequest('/getProjects?user=' + TEST_USER, False)
        
    def test_1150_getProject(self):
        self.makeRequest('/getProject?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1200_cloneProject(self):
        self.makeRequest('/cloneProject?user=' + TEST_USER + '&project=' + TEST_PROJECT, False)

    def test_1300_createProjectGroup(self):
        _dict = self.makeRequest('/createProjectGroup?user=' + TEST_USER + '&project=' + TEST_PROJECT + '&copies=5&blmValues=0.1,0.2,0.3,0.4,0.5', False)
        global projects
        # get the names of the projects so we can delete them in the next test
        projects = ",".join([i['projectName'] for i in _dict['data']])

    def test_1400_deleteProjects(self): 
        self.makeRequest('/deleteProjects?projectNames=' + projects, False)

    def test_1500_renameProject(self):
        self.makeRequest('/renameProject?user=' + TEST_USER + '&project=' + TEST_PROJECT + "&newName=wibble", False)
 
    def test_1600_updateProjectParameters(self):
        body = urllib.parse.urlencode({"user":TEST_USER,"project":TEST_IMPORT_PROJECT, 'COLORCODE':'wibble'})
        self.makeRequest('/updateProjectParameters', False, method="POST", body=body)

    def test_1650_upgradeProject(self):
        self.makeRequest('/upgradeProject?user=' + TEST_USER + '&project=wibble', False)

    def test_1800_listProjectsForFeature(self):
        self.makeRequest('/listProjectsForFeature?feature_class_id=63407942', False)

    def test_1900_listProjectsForPlanningGrid(self):
        self.makeRequest('/listProjectsForPlanningGrid?feature_class_name=pu_89979654c5d044baa27b6008f9d06', False)

    def test_2000_getCountries(self):
        self.makeRequest('/getCountries', False)

    def test_2100_getPlanningUnitGrids(self):
        self.makeRequest('/getPlanningUnitGrids', False)

    def test_2200_deletePlanningUnitGrid(self):
        self.makeRequest('/deletePlanningUnitGrid?planning_grid_name=pu_and_terrestrial_square_50', False)

    def test_2300_createPlanningUnitGrid(self):
        self.makeWebSocketRequest('/createPlanningUnitGrid?iso3=AND&domain=Terrestrial&areakm2=50&shape=square', False)

    def test_2400_uploadTilesetToMapBox(self):
        self.makeRequest('/uploadTilesetToMapBox?feature_class_name=pu_and_terrestrial_square_50&mapbox_layer_name=square', False)
    
    def test_2500_getUser(self):
        self.makeRequest('/getUser?user=' + TEST_USER, False)

    def test_2600_getUsers(self):
        self.makeRequest('/getUsers', False)

    def test_2700_updateUserParameters(self):
        body = urllib.parse.urlencode({"user":TEST_USER, 'EMAIL':'wibble2'})
        self.makeRequest('/updateUserParameters', False, method="POST", body=body)

    def test_2800_getFeature(self):
        self.makeRequest('/getFeature?oid=63407942', False)
        
    def test_2900_uploadShapefile(self):
        #get the path to the file to upload
        testFile = TEST_DATA_FOLDER + TEST_ZIP_SHP_MULTIPLE
        self.uploadFile(testFile, {'name': 'whatever', 'description': 'whatever2', 'filename': TEST_ZIP_SHP_MULTIPLE}, False)
        # TODO The following is a hack as I cant upload a zip file as a binary file through the API, so all subsequent operations on the zip shapefile fail
        copyTestData(TEST_ZIP_SHP_MULTIPLE)

    def test_3000_unzipShapefile(self):
        self.makeRequest('/unzipShapefile?filename=' + TEST_ZIP_SHP_MULTIPLE, False)

    def test_3100_getShapefileFieldnames(self):
        self.makeRequest('/getShapefileFieldnames?filename=' + TEST_ZIP_SHP_MULTIPLE[:-4] + ".shp", False)

    def test_3200_importFeatures(self):
        #non-existing shapefile
        self.makeWebSocketRequest('/importFeatures?zipfile=ignored&shapefile=nonexisting', True)

    def test_3300_importFeatures(self):
        #import multiple features 
        features = self.makeWebSocketRequest('/importFeatures?zipfile=' + TEST_ZIP_SHP_MULTIPLE + '&shapefile=' + TEST_ZIP_SHP_MULTIPLE[:-4] + ".shp&splitfield=sp_duplica", False)
        global fcns
        #get the feature class names of those that have been imported
        fcns = [feature['feature_class_name'] for feature in features if feature['status'] == 'FeatureCreated']
        for f in fcns:
            self.makeRequest('/deleteFeature?feature_name=' + f, False)

    def test_3400_importFeatures(self):
        #import features with invalid geometries
        #copy the test data
        copyTestData(TEST_ZIP_SHP_INVALID_GEOM)
        #unzip it
        self.makeRequest('/unzipShapefile?filename=' + TEST_ZIP_SHP_INVALID_GEOM, False)
        #import the features
        features = self.makeWebSocketRequest('/importFeatures?zipfile=' + TEST_ZIP_SHP_INVALID_GEOM + '&shapefile=' + TEST_ZIP_SHP_INVALID_GEOM[:-4] + ".shp", True)
        #delete the shapefile and zipped shapefile
        self.makeRequest('/deleteShapefile?zipfile=' + TEST_ZIP_SHP_INVALID_GEOM + '&shapefile=' + TEST_ZIP_SHP_INVALID_GEOM[:-4] + ".shp", False)

    def test_3500_importFeatures(self):
        #import a zipped shapefile with missing files
        #copy the test data
        copyTestData(TEST_ZIP_SHP_MISSING_FILE)
        #unzip it
        self.makeRequest('/unzipShapefile?filename=' + TEST_ZIP_SHP_MISSING_FILE, False)
        #import the features
        features = self.makeWebSocketRequest('/importFeatures?zipfile=' + TEST_ZIP_SHP_MISSING_FILE + '&shapefile=' + TEST_ZIP_SHP_MISSING_FILE[:-4] + ".shp", True)
        #delete the shapefile and zipped shapefile
        self.makeRequest('/deleteShapefile?zipfile=' + TEST_ZIP_SHP_MISSING_FILE + '&shapefile=pulayer_costt.shp', False)

    def test_3600_deleteShapefile(self):
        self.makeRequest('/deleteShapefile?zipfile=' + TEST_ZIP_SHP_MULTIPLE + '&shapefile=' + TEST_ZIP_SHP_MULTIPLE[:-4] + ".shp", False)

    def test_3700_deleteFeature(self):
        #delete the imported features
        for f in fcns:
            self.makeRequest('/deleteFeature?feature_name=' + f, False)

    def test_3800_createFeatureFromLinestring(self):
        body = urllib.parse.urlencode({"name": "wibble","description":"wibble2","linestring":"Linestring(-175.3421006344285 -20.69048933878365,-175.4011153142698 -20.86450796169632,-175.001631327652 -20.868749810194487,-174.98801255538095 -20.60977871499442,-175.3421006344285 -20.69048933878365)"})
        f = self.makeRequest('/createFeatureFromLinestring', False, method="POST", body=body)
        #delete the feature class
        self.makeRequest('/deleteFeature?feature_name=' + f['feature_class_name'], False)

    def test_3900_getFeaturePlanningUnits(self):
        self.makeRequest('/getFeaturePlanningUnits?user=admin&project=Start%20project&oid=63408475', False)

    def test_4000_getPlanningUnitsCostData(self):
        self.makeRequest('/getPlanningUnitsCostData?user=admin&project=Start%20project', False)

    def test_4100_getPUData(self):
        self.makeRequest('/getPUData?user=admin&project=Start%20project&puid=10561', False)

    def test_4200_getAllSpeciesData(self):
        self.makeRequest('/getAllSpeciesData', False)

    def test_4300_getResults(self):
        self.makeRequest('/getResults?user=admin&project=Start%20project', False)

    def test_4400_getSolution(self):
        self.makeRequest('/getSolution?user=admin&project=Start%20project&solution=1', False)

    def test_4500_importGBIFData(self):
        #delete the feature if it already exists
        self.makeRequest('/deleteFeature?feature_name=gbif_2486629', False)
        self.makeWebSocketRequest('/importGBIFData?taxonKey=2486629&scientificName=Clytorhynchus%20nigrogularis', False)

    def test_4600_dismissNotification(self):
        self.makeRequest('/dismissNotification?user=admin&notificationid=1', False)

    def test_4700_resetNotifications(self):
        self.makeRequest('/resetNotifications?user=admin', False)

    def test_4800_addParameter(self):
        self.makeRequest('/addParameter?type=user&key=REPORTUNITS&value=Ha', False)

    def test_4850_deleteProject(self):
        self.makeRequest('/deleteProject?user=' + TEST_USER + '&project=wibble', False)

    def test_9999_deleteUser(self):
        self.makeRequest('/deleteUser?user=' + TEST_USER, False)
        
    # def test_(self):
    #     self.makeRequest('/')

            # ("/uploadFile", uploadFile),
            # ("/importPlanningUnitGrid", importPlanningUnitGrid),
            # ("/createFeaturePreprocessingFileFromImport", createFeaturePreprocessingFileFromImport),
            # ("/logout", logout),
            # ("/resendPassword", resendPassword),

            # ("/stopProcess", stopProcess), #cant easily unit test
            # ("/updateWDPA", updateWDPA), #cant easily unit test at the moment because we dont have the april 2020 wdpa online
            # ("/shutdown", shutdown), #not a good idea to test this!
            # ("/block", block), #no unit test required
        
        